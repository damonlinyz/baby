import unittest,tempfile,sys,os,datetime,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import domain as d
class DomainTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();d.DB=Path(self.tmp.name)/'test.sqlite3';d.init()
  with d.transaction() as c:
   self.dad=d.auth_register(c,{'login':'dad','name':'爸爸','password':'long-password-123'})
   self.mom=d.auth_register(c,{'login':'mom','name':'妈妈','password':'long-password-123'})
   self.other=d.auth_register(c,{'login':'other','name':'其他人','password':'long-password-123'})
   self.fid=d.create_family(c,self.dad,{'name':'测试家庭'})['family_id']
   inv=d.mutate(c,self.dad,self.fid,'invite.create',{'role':'mom'});d.join(c,self.mom,inv['invite_code'])
 def tearDown(self):self.tmp.cleanup()
 def call(self,user,action,data):
  with d.transaction() as c:return d.mutate(c,user,self.fid,action,data)
 def snap(self,user):
  with d.transaction() as c:return d.snapshot(c,user,self.fid)
 def test_no_cross_family_access(self):
  with self.assertRaises(d.Problem) as e:self.snap(self.other)
  self.assertEqual(e.exception.status,403)
 def test_real_approval(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  t=self.snap(self.dad)['tasks'][0];r=self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==r['approval_id'])
  with self.assertRaises(d.Problem):self.call(self.dad,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  self.assertEqual(next(x for x in self.snap(self.dad)['tasks'] if x['id']==t['id'])['status'],'done')
 def test_policy_cannot_be_bypassed(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'selected'})
  f=self.snap(self.dad)['family'];r=self.call(self.dad,'family.update',{'version':f['version'],'mode':'off'})
  self.assertTrue(r['pending']);self.assertEqual(self.snap(self.dad)['family']['mode'],'selected')
 def test_stale_write_is_rejected(self):
  t=self.snap(self.dad)['tasks'][0];self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  with self.assertRaises(d.Problem) as e:self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'todo'})
  self.assertEqual(e.exception.status,409)
 def test_health_is_subject_controlled(self):
  with self.assertRaises(d.Problem):self.call(self.dad,'pregnancy.create',{'consent':True})
  self.call(self.mom,'pregnancy.create',{'consent':True});self.assertTrue(self.snap(self.dad)['pregnancy']['restricted'])
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  p=self.snap(self.dad)['pregnancy'];self.assertEqual(p['state'],'dating')
  with self.assertRaises(d.Problem):self.call(self.dad,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=100))})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':False});self.assertTrue(self.snap(self.dad)['pregnancy']['restricted'])
 def test_lifecycle_and_repeat_birth(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=100))})
  p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':2})
  p=self.snap(self.mom)['pregnancy'];self.assertEqual(p['state'],'postpartum')
  with self.assertRaises(d.Problem):self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today())})
  with d.transaction() as c:self.assertEqual(len(d.rows(c,'baby',self.fid)),2)
 def test_member_cannot_fake_other_completion(self):
  inv=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,inv['invite_code'])
  self.assertEqual(self.snap(self.other)['tasks'],[])
  t=self.snap(self.dad)['tasks'][0]
  with self.assertRaises(d.Problem):self.call(self.other,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
 def test_invitation_single_use(self):
  inv=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,inv['invite_code'])
  with d.transaction() as c:
   with self.assertRaises(d.Problem):d.join(c,self.other,inv['invite_code'])
 def test_login_hash(self):
  p=d.password_hash('a-long-password');self.assertTrue(d.password_ok('a-long-password',p));self.assertFalse(d.password_ok('wrong-password',p));self.assertNotIn('a-long-password',p)
 def test_pending_payload_edit_invalidates_approval(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  t=self.snap(self.dad)['tasks'][0];self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  t=self.snap(self.dad)['tasks'][0];self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'title':'更新后的任务'})
  self.assertFalse(any(a['status']=='pending' for a in self.snap(self.mom)['approvals']))
 def pending_task(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  t=self.snap(self.dad)['tasks'][0]
  r=self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  return t,next(a for a in self.snap(self.mom)['approvals'] if a['id']==r['approval_id'])
 def test_expired_confirmation_can_be_resubmitted(self):
  t,a=self.pending_task()
  with d.transaction() as c:
   a['expires_at']=d.now();d.put(c,'approval',a,self.fid)
  s=self.snap(self.dad);t=next(x for x in s['tasks'] if x['id']==t['id'])
  self.assertEqual(t['status'],'in_progress')
  self.assertEqual(next(x for x in s['approvals'] if x['id']==a['id'])['status'],'expired')
  r=self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  a=next(x for x in self.snap(self.mom)['approvals'] if x['id']==r['approval_id'])
  self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  self.assertEqual(next(x for x in self.snap(self.dad)['tasks'] if x['id']==t['id'])['status'],'done')
 def test_policy_change_cancels_pending_completions(self):
  t,a=self.pending_task();f=self.snap(self.dad)['family']
  r=self.call(self.dad,'family.update',{'version':f['version'],'mode':'off'})
  policy=next(x for x in self.snap(self.mom)['approvals'] if x['id']==r['approval_id'])
  self.call(self.mom,'approval.decide',{'id':policy['id'],'version':policy['version'],'decision':'approved'})
  s=self.snap(self.dad)
  self.assertEqual(s['family']['mode'],'off')
  self.assertEqual(next(x for x in s['tasks'] if x['id']==t['id'])['status'],'in_progress')
  with self.assertRaises(d.Problem):self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
 def test_template_redating_invalidates_only_changed_dates(self):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  p=self.snap(self.mom)['pregnancy'];edd=str(d.today()+datetime.timedelta(days=100))
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':edd})
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  t=next(x for x in self.snap(self.dad)['tasks'] if x.get('template_key'))
  r=self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'pause'})
  same=next(x for x in self.snap(self.dad)['tasks'] if x['id']==t['id'])
  self.assertEqual(same['version'],t['version']+1)
  self.assertEqual(same['status'],'pending')
  p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.date(edd)+datetime.timedelta(days=5))})
  s=self.snap(self.dad);changed=next(x for x in s['tasks'] if x['id']==t['id'])
  self.assertEqual(changed['status'],'in_progress')
  self.assertEqual(d.date(changed['due_date']),d.date(t['due_date'])+datetime.timedelta(days=5))
  self.assertEqual(next(x for x in s['approvals'] if x['id']==r['approval_id'])['status'],'cancelled')
if __name__=='__main__':unittest.main(verbosity=2)
