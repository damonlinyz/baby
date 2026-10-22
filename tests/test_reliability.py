"""Regression scenarios for removal, revoked access and command retries."""
import datetime
import concurrent.futures
import threading
import unittest
import test_domain
import domain as d


class ReliabilityTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def add_member(self):
  inv=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,inv['invite_code'])

 def health_setup(self):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  for scope in ('health_read','health_edit'):
   self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':scope,'enabled':True})
  p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=7))})
  return self.snap(self.mom)['pregnancy']

 def appointment(self):
  return self.call(self.dad,'appointment.create',{'title':'合成预约','date':str(d.today()+datetime.timedelta(days=1))})['id']

 def test_revoked_reader_cannot_read_own_delegated_appointment(self):
  self.health_setup();aid=self.appointment();d.tick()
  self.assertTrue(self.snap(self.dad)['notifications'])
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_read','enabled':False})
  s=self.snap(self.dad)
  self.assertEqual(s['appointments'],[]);self.assertEqual(s['pregnancies'],[])
  self.assertFalse(any(t.get('private_health_anchor') for t in s['tasks']))
  self.assertEqual(s['notifications'],[])
  with d.transaction() as c:
   a=d.get(c,'appointment',aid);a['reminded']=False;d.put(c,'appointment',a,self.fid)
   for n in d.rows(c,'notification',self.fid):c.execute("DELETE FROM records WHERE kind='notification' AND id=?",(n['id'],))
  d.tick()
  with d.transaction() as c:self.assertEqual(d.rows(c,'notification',self.fid),[])

 def test_revoked_editor_cannot_modify_appointment(self):
  self.health_setup();aid=self.appointment()
  a=next(x for x in self.snap(self.dad)['appointments'] if x['id']==aid)
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_edit','enabled':False})
  with self.assertRaises(d.Problem) as err:self.call(self.dad,'appointment.update',{'id':aid,'version':a['version'],'status':'done'})
  self.assertEqual(err.exception.status,403)

 def test_revocation_cancels_private_completion(self):
  self.health_setup();f=self.snap(self.dad)['family']
  self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  t=next(x for x in self.snap(self.dad)['tasks'] if x.get('private_health_anchor'))
  r=self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_read','enabled':False})
  s=self.snap(self.mom)
  self.assertEqual(next(x for x in s['tasks'] if x['id']==t['id'])['status'],'in_progress')
  self.assertEqual(next(x for x in s['approvals'] if x['id']==r['approval_id'])['status'],'cancelled')

 def test_delete_subject_removes_derived_records_and_keeps_family_usable(self):
  p=self.health_setup();self.appointment();d.tick()
  custom=self.call(self.dad,'task.create',{'title':'普通家务'})['id']
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  t=next(x for x in self.snap(self.dad)['tasks'] if x.get('private_health_anchor'))
  self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  with d.transaction() as c:
   d.new_session(c,self.mom)
   d.command(c,self.dad,self.fid,'task.create','cached-task',{'title':'普通待办'})
   d.delete_account(c,self.mom,'long-password-123')
  s=self.snap(self.dad)
  self.assertIsNone(s['pregnancy']);self.assertEqual(s['family']['mode'],'off')
  self.assertEqual(s['appointments'],[]);self.assertEqual(s['approvals'],[])
  self.assertIsNone(next(x for x in s['tasks'] if x['id']==custom)['pregnancy_id'])
  with d.transaction() as c:
   for kind in ('pregnancy','task','appointment','baby','notification','approval'):
    self.assertFalse(any(x.get('pregnancy_id')==p['id'] or x.get('ref')==t['id'] for x in d.rows(c,kind,self.fid)),kind)
   self.assertEqual(c.execute('SELECT count(*) FROM sessions WHERE user_id=?',(self.mom,)).fetchone()[0],0)
   self.assertEqual(c.execute('SELECT count(*) FROM command_requests WHERE family_id=? AND invalidated=0',(self.fid,)).fetchone()[0],0)
  with self.assertRaises(d.Problem) as err:
   with d.transaction() as c:d.command(c,self.dad,self.fid,'task.create','cached-task',{'title':'普通待办'})
  self.assertEqual(err.exception.status,409)
  self.assertEqual(len([x for x in self.snap(self.dad)['tasks'] if x['title']=='普通待办']),1)
  d.tick()  # No dangling pregnancy references in the scheduler.

 def test_delete_coordinator_and_bad_password_are_rejected(self):
  for user,pw,status in ((self.mom,'incorrect',403),(self.dad,'long-password-123',409)):
   with self.assertRaises(d.Problem) as err:
    with d.transaction() as c:d.delete_account(c,user,pw)
   self.assertEqual(err.exception.status,status)
  self.assertEqual(len(self.snap(self.dad)['members']),2)

 def test_new_transfer_cancels_old_and_cannot_overwrite_current_coordinator(self):
  self.add_member()
  first=self.call(self.dad,'family.transfer',{'user_id':self.other})['transfer_id']
  with d.transaction() as c:old=d.get(c,'transfer',first)
  second=self.call(self.dad,'family.transfer',{'user_id':self.other})['transfer_id']
  with self.assertRaises(d.Problem):self.call(self.other,'transfer.accept',{'id':first,'version':old['version']})
  t=next(x for x in self.snap(self.other)['transfers'] if x['id']==second)
  self.call(self.other,'transfer.accept',{'id':second,'version':t['version']})
  self.assertEqual(self.snap(self.other)['family']['coordinator'],self.other)
  # Even a historical pending row cannot replace the current coordinator.
  with d.transaction() as c:
   old['status']='pending';old=d.put(c,'transfer',old,self.fid)
  with self.assertRaises(d.Problem):self.call(self.other,'transfer.accept',{'id':first,'version':old['version']})
  with d.transaction() as c:
   self.assertEqual(c.execute("SELECT count(*) FROM memberships WHERE family_id=? AND active=1 AND role='dad'",(self.fid,)).fetchone()[0],1)

 def test_removed_transfer_recipient_cannot_accept_after_rejoining(self):
  self.add_member();tid=self.call(self.dad,'family.transfer',{'user_id':self.other})['transfer_id']
  self.call(self.dad,'member.remove',{'user_id':self.other});self.add_member()
  with d.transaction() as c:t=d.get(c,'transfer',tid)
  self.assertEqual(t['status'],'cancelled')
  with self.assertRaises(d.Problem):self.call(self.other,'transfer.accept',{'id':tid,'version':t['version']})

 def test_mother_leaving_resets_pending_task(self):
  t,a=test_domain.DomainTests.pending_task(self)
  self.call(self.mom,'member.leave',{})
  s=self.snap(self.dad)
  self.assertEqual(s['family']['mode'],'off')
  self.assertEqual(next(x for x in s['tasks'] if x['id']==t['id'])['status'],'in_progress')
  self.assertEqual(next(x for x in s['approvals'] if x['id']==a['id'])['status'],'cancelled')

 def test_transfer_revokes_old_edit_grant_and_invites(self):
  self.add_member();self.health_setup()
  inv=self.call(self.dad,'invite.create',{'role':'member'})
  tid=self.call(self.dad,'family.transfer',{'user_id':self.other})['transfer_id']
  with d.transaction() as c:t=d.get(c,'transfer',tid)
  self.call(self.other,'transfer.accept',{'id':tid,'version':t['version']})
  pid=self.snap(self.mom)['pregnancy']['id']
  with d.transaction() as c:
   p=d.get(c,'pregnancy',pid)
   self.assertFalse(d.can_edit_health(c,self.fid,self.dad,p))
   self.assertFalse(d.can_edit_health(c,self.fid,self.other,p))
   with self.assertRaises(d.Problem):d.join(c,'unused-user',inv['invite_code'])

 def test_command_retry_binds_family_action_and_payload(self):
  def command(fid,action,data):
   with d.transaction() as c:return d.command(c,self.dad,fid,action,'same-request',data)
  first=command(self.fid,'task.create',{'title':'只创建一次','note':'备注'})
  self.assertEqual(first,command(self.fid,'task.create',{'note':'备注','title':'只创建一次'}))
  with d.transaction() as c:other=d.create_family(c,self.dad,{'name':'另一个家'})['family_id']
  for fid,action,data in ((self.fid,'task.create',{'title':'改变内容'}),(other,'task.create',{'title':'只创建一次','note':'备注'}),(self.fid,'item.create',{'title':'只创建一次','note':'备注'})):
   with self.assertRaises(d.Problem) as err:command(fid,action,data)
   self.assertEqual(err.exception.status,409)
  self.assertEqual(len(self.snap(self.dad)['tasks']),5)

 def test_legacy_unbound_request_is_not_replayed(self):
  with d.transaction() as c:c.execute('INSERT INTO requests VALUES(?,?,?,?)',(self.dad,'old-key','{}',d.now()))
  with self.assertRaises(d.Problem) as err:
   with d.transaction() as c:d.command(c,self.dad,self.fid,'task.create','old-key',{'title':'旧请求'})
  self.assertEqual(err.exception.status,409)

 def test_concurrent_retry_creates_one_task(self):
  barrier=threading.Barrier(2)
  def submit():
   barrier.wait(timeout=5)
   with d.transaction() as c:return d.command(c,self.dad,self.fid,'task.create','concurrent-create',{'title':'并发重试'})
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
   results=list(pool.map(lambda _:submit(),range(2)))
  self.assertEqual(results[0],results[1])
  self.assertEqual(len([x for x in self.snap(self.dad)['tasks'] if x['title']=='并发重试']),1)

 def test_concurrent_transfer_accepts_once(self):
  self.add_member();tid=self.call(self.dad,'family.transfer',{'user_id':self.other})['transfer_id']
  with d.transaction() as c:t=d.get(c,'transfer',tid)
  barrier=threading.Barrier(2)
  def accept():
   barrier.wait(timeout=5)
   try:
    self.call(self.other,'transfer.accept',{'id':tid,'version':t['version']});return 200
   except d.Problem as err:return err.status
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
   results=list(pool.map(lambda _:accept(),range(2)))
  self.assertEqual(sorted(results),[200,409])
  with d.transaction() as c:
   self.assertEqual(d.get(c,'family',self.fid)['coordinator'],self.other)
   self.assertEqual(c.execute("SELECT count(*) FROM memberships WHERE family_id=? AND active=1 AND role='dad'",(self.fid,)).fetchone()[0],1)
