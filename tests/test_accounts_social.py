import hashlib
import unittest
from unittest.mock import patch
import test_domain
import domain as d
import accounts


class AccountTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 def proof(self):return {'password':'long-password-123'}
 def test_recovery_is_hash_only_single_use_and_revokes_all_sessions(self):
  with d.transaction() as c:
   token,_=d.new_session(c,self.dad);other,_=d.new_session(c,self.dad)
   code=accounts.issue_recovery(c,self.dad,self.proof())['code']
   self.assertNotIn(code,d.dumps(d.rows(c,'account_recovery')))
   accounts.recover(c,{'login':'DAD','code':code,'new_password':'new-password-123'})
   self.assertFalse(accounts.settings(c,self.dad)['recovery_ready'])
   self.assertTrue(d.password_ok('new-password-123',c.execute('SELECT password FROM users WHERE id=?',(self.dad,)).fetchone()[0]))
   for t in (token,other):
    with self.assertRaises(d.Problem):d.session(c,t)
   with self.assertRaises(d.Problem):accounts.recover(c,{'login':'dad','code':code,'new_password':'another-password'})
 def test_rotating_code_invalidates_previous_and_unknown_login_is_generic(self):
  with d.transaction() as c:
   first=accounts.issue_recovery(c,self.dad,self.proof())['code'];second=accounts.issue_recovery(c,self.dad,self.proof())['code']
   failures=[]
   for login,code in [('dad',first),('unknown',second),('mom',second)]:
    with self.assertRaises(d.Problem) as err:accounts.recover(c,{'login':login,'code':code,'new_password':'safe-new-password'})
    failures.append(err.exception.message)
   self.assertEqual(len(set(failures)),1)
 def test_change_requires_proof_and_recovery_is_revoked(self):
  with d.transaction() as c:
   accounts.issue_recovery(c,self.dad,self.proof());d.new_session(c,self.dad)
   with self.assertRaises(d.Problem):accounts.change_password(c,self.dad,{'password':'wrong','new_password':'a-safe-new-password'})
   accounts.change_password(c,self.dad,{**self.proof(),'new_password':'a-safe-new-password'})
   self.assertEqual(c.execute('SELECT count(*) FROM sessions WHERE user_id=?',(self.dad,)).fetchone()[0],0)
   self.assertFalse(accounts.settings(c,self.dad)['recovery_ready'])
 def test_fresh_wechat_proof_cannot_bind_a_different_identity(self):
  identity={'appid':'test-app','openid':'verified-user'}
  with d.transaction() as c:
   d.put(c,'wechat_identity',{'id':'wx','user_id':self.mom,**identity})
   with self.assertRaises(d.Problem):accounts.change_password(c,self.dad,{'new_password':'a-safe-new-password'},identity)
   accounts.change_password(c,self.mom,{'new_password':'a-safe-new-password'},identity)
   with self.assertRaises(d.Problem):accounts.prove(c,self.mom,{},dict(identity,appid='other-app'))
 def test_wechat_delete_obeys_transfer_gate_and_tombstones(self):
  with d.transaction() as c:
   for user in (self.mom,self.dad):
    identity={'appid':'test','openid':user};d.put(c,'wechat_identity',{'id':user,'user_id':user,**identity})
    if user==self.dad:
     with self.assertRaises(d.Problem) as err:accounts.delete_verified(c,user,{'confirmed':True},identity)
     self.assertEqual(err.exception.status,409)
    else:
     accounts.issue_recovery(c,user,{},identity)
     accounts.delete_verified(c,user,{'confirmed':True},identity)
     self.assertIsNone(c.execute('SELECT 1 FROM users WHERE id=?',(user,)).fetchone())
     self.assertIsNotNone(c.execute('SELECT 1 FROM deletion_log.tombstones WHERE user_id=?',(user,)).fetchone())
     self.assertEqual(d.rows(c,'account_recovery'),[])


class FeedbackTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def done(self):
  t=self.snap(self.dad)['tasks'][0];self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  return self.snap(self.mom)['feedback_feed'][0]
 def test_like_is_explicit_idempotent_and_comment_command_replay_is_unique(self):
  item=self.done()
  for _ in range(2):self.call(self.mom,'feedback.like',{'event_id':item['id'],'enabled':True})
  with d.transaction() as c:
   args=(self.mom,self.fid,'feedback.comment','one-command',{'event_id':item['id'],'text':'谢谢你具体的分担'})
   first=d.command(c,*args);self.assertEqual(first,d.command(c,*args))
  feed=self.snap(self.dad)['feedback_feed'][0];self.assertEqual(feed['likes'],1);self.assertEqual(len(feed['comments']),1)
  self.call(self.mom,'feedback.like',{'event_id':item['id'],'enabled':False});self.assertEqual(self.snap(self.dad)['feedback_feed'][0]['likes'],0)
 def test_other_member_cannot_read_or_comment_on_unassigned_completion(self):
  item=self.done();invite=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,invite['invite_code'])
  self.assertEqual(self.snap(self.other)['feedback_feed'],[])
  with self.assertRaises(d.Problem):self.call(self.other,'feedback.comment',{'event_id':item['id'],'text':'越权'})
 def test_undo_recomplete_changes_event_and_cannot_revive_old_comments(self):
  item=self.done();self.call(self.mom,'feedback.comment',{'event_id':item['id'],'text':'旧留言'})
  for status in ('todo','done'):
   t=next(t for t in self.snap(self.dad)['tasks'] if t['id']==item['target']);self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':status})
  fresh=self.snap(self.dad)['feedback_feed'][0];self.assertNotEqual(item['id'],fresh['id']);self.assertEqual(fresh['comments'],[])
  with self.assertRaises(d.Problem):self.call(self.mom,'feedback.like',{'event_id':item['id'],'enabled':True})
 def test_member_leave_rejoin_cannot_revive_feedback(self):
  item=self.done();self.call(self.mom,'feedback.comment',{'event_id':item['id'],'text':'原来的留言'})
  self.call(self.mom,'member.leave',{})
  with d.transaction() as c:self.assertEqual(d.rows(c,'feedback',self.fid),[])
  invite=self.call(self.dad,'invite.create',{'role':'mom'})
  with d.transaction() as c:d.join(c,self.mom,invite['invite_code'])
  self.assertEqual(self.snap(self.dad)['feedback_feed'][0]['comments'],[])
 def test_deletion_and_author_permissions(self):
  item=self.done();comment=self.call(self.dad,'feedback.comment',{'event_id':item['id'],'text':'自己的记录'})
  with self.assertRaises(d.Problem):self.call(self.mom,'feedback.delete',{'event_id':item['id'],'id':comment['id']})
  self.call(self.dad,'feedback.delete',{'event_id':item['id'],'id':comment['id']});self.assertEqual(self.snap(self.mom)['feedback_feed'][0]['comments'],[])
 def test_pending_completion_does_not_publish_feedback(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'selected'})
  t=self.snap(self.dad)['tasks'][0];self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  self.assertEqual(self.snap(self.mom)['feedback_feed'],[])
 def test_health_revocation_clears_comment_body_before_regrant(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  with d.transaction() as c:
   p=d.get(c,'family',self.fid)['active_pregnancy_id'];t=d.rows(c,'task',self.fid)[0]
   t.update(private_health_anchor=True,pregnancy_id=p,status='done',completed_at=d.now());t=d.put(c,'task',t,self.fid);d.engagement.record(c,self.fid,'task',t)
  item=self.snap(self.dad)['feedback_feed'][0];self.call(self.dad,'feedback.comment',{'event_id':item['id'],'text':'需清理的敏感留言'})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':False})
  with d.transaction() as c:self.assertEqual(d.rows(c,'feedback',self.fid),[])
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  self.assertEqual(self.snap(self.dad)['feedback_feed'][0]['comments'],[])
