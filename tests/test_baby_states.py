import datetime
import unittest
import test_domain
import domain as d
import lifecycle


class BabyStateTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def prepare(self,outcomes=True):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=100))})
  p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()-datetime.timedelta(days=5)),'babies':2,**({'birth_outcomes':['growing','growing'],'guardian_consent':True} if outcomes else {})})
  return self.snap(self.mom)['babies']
 def data(self,baby,status='lost'):return {'id':baby['id'],'version':baby['version'],'status':status,'date':str(d.today()),'guardian_consent':True}

 def test_twins_have_independent_outcomes_and_maternal_recovery_is_preserved(self):
  babies=self.prepare();data=self.data(babies[0])
  maternal=next(t for t in self.snap(self.mom)['tasks'] if t.get('template_key')=='rest-plan')
  self.call(self.dad,'task.update',{'id':maternal['id'],'version':maternal['version'],'assignee':self.mom})
  maternal=next(t for t in self.snap(self.mom)['tasks'] if t['id']==maternal['id'])
  self.call(self.mom,'task.update',{'id':maternal['id'],'version':maternal['version'],'status':'done'})
  with d.transaction() as c:
   before=[tuple(r) for r in c.execute('SELECT * FROM records ORDER BY kind,id')]
   preview=lifecycle.preview_baby(c,self.fid,self.mom,data)
   self.assertEqual(before,[tuple(r) for r in c.execute('SELECT * FROM records ORDER BY kind,id')])
  self.assertEqual(len(preview['cancelled_tasks']),3)
  self.call(self.mom,'baby.update',dict(data,preview_token=preview['preview_token']))
  s=self.snap(self.mom);self.assertEqual(s['pregnancy']['state'],'postpartum');self.assertFalse(s['pregnancy']['paused'])
  self.assertEqual({b['id']:b['status'] for b in s['babies']},{babies[0]['id']:'lost',babies[1]['id']:'growing'})
  self.assertEqual(next(t for t in s['tasks'] if t['id']==maternal['id'])['status'],'done')
  self.assertTrue(all(t['status']=='cancelled' for t in s['tasks'] if t.get('template_key') in lifecycle.QUIET_TEMPLATE_KEYS))
  self.assertIn('产后',s['pregnancy']['timeline']['title'])

 def test_child_sharing_requires_own_scope_and_guardian_confirmation(self):
  self.prepare();self.assertEqual(self.snap(self.dad)['babies'],[])
  with self.assertRaises(d.Problem):self.call(self.mom,'consent.update',{'scope':'baby_read','grantee':self.dad,'enabled':True})
  self.call(self.mom,'consent.update',{'scope':'baby_read','grantee':self.dad,'enabled':True,'guardian_consent':True})
  self.assertEqual(len(self.snap(self.dad)['babies']),2)
  self.call(self.mom,'consent.update',{'scope':'baby_read','grantee':self.dad,'enabled':False})
  self.assertEqual(self.snap(self.dad)['babies'],[])
  self.assertFalse(self.snap(self.dad)['pregnancy'].get('restricted',False))
  self.assertFalse(any(t.get('template_key') in lifecycle.BABY_TEMPLATE_KEYS for t in self.snap(self.dad)['tasks']))

 def test_health_delegate_cannot_record_child_status(self):
  baby=self.prepare()[0]
  self.call(self.mom,'consent.update',{'scope':'health_edit','grantee':self.dad,'enabled':True})
  with self.assertRaises(d.Problem) as err:self.call(self.dad,'baby.update',self.data(baby))
  self.assertEqual(err.exception.status,403)
  with self.assertRaises(d.Problem):self.call(self.mom,'baby.update',dict(self.data(baby),guardian_consent=False))

 def test_lost_record_needs_explicit_correction_before_changing_status(self):
  baby=self.prepare()[0];self.call(self.mom,'baby.update',self.data(baby))
  baby=self.snap(self.mom)['babies'][0]
  with self.assertRaises(d.Problem):self.call(self.mom,'baby.update',self.data(baby,'growing'))
  self.call(self.mom,'baby.update',dict(self.data(baby,'growing'),correct_previous=True,reason='合成测试：原状态为误录'))
  self.assertEqual(self.snap(self.mom)['babies'][0]['status'],'growing')
  with d.transaction() as c:self.assertEqual(len([e for e in d.rows(c,'baby_event',self.fid) if e['event']=='status_record']),2)

 def test_stale_preview_and_invalid_dates_are_rejected(self):
  baby=self.prepare()[0];data=self.data(baby)
  with d.transaction() as c:preview=lifecycle.preview_baby(c,self.fid,self.mom,data)
  self.call(self.mom,'baby.update',self.data(baby,'hospitalized'))
  with self.assertRaises(d.Problem) as err:self.call(self.mom,'baby.update',dict(data,preview_token=preview['preview_token']))
  self.assertEqual(err.exception.status,409)
  baby=self.snap(self.mom)['babies'][0]
  for date in (str(d.today()+datetime.timedelta(days=1)),str(d.today()-datetime.timedelta(days=10))):
   with self.assertRaises(d.Problem):self.call(self.mom,'baby.update',dict(self.data(baby),date=date))

 def test_child_record_does_not_allow_whole_birth_to_be_reclassified(self):
  baby=self.prepare()[0];self.call(self.mom,'baby.update',self.data(baby,'hospitalized'));p=self.snap(self.mom)['pregnancy']
  with self.assertRaises(d.Problem):self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'correct_birth_record','birth_event_id':p['birth_event_id'],'confirm_correction':True,'reason':'合成测试'})

 def test_corrected_birth_date_keeps_previous_corrected_child_records_unchanged(self):
  self.prepare();p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'correct_birth_record','birth_event_id':p['birth_event_id'],'confirm_correction':True,'reason':'合成误录'})
  old=self.snap(self.mom)['babies'];p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':1});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'correct_birth','date':str(d.today()-datetime.timedelta(days=1))})
  after={b['id']:b for b in self.snap(self.mom)['babies']}
  for b in old:self.assertEqual(after[b['id']]['birth_date'],b['birth_date'])

 def test_deletion_removes_child_event_history(self):
  baby=self.prepare()[0];self.call(self.mom,'baby.update',self.data(baby))
  with d.transaction() as c:
   d.delete_account(c,self.mom,'long-password-123');self.assertEqual(d.rows(c,'baby_event',self.fid),[]);self.assertEqual(d.rows(c,'baby',self.fid),[])

 def test_legacy_birth_stays_unconfirmed_until_each_guardian_confirmation(self):
  babies=self.prepare(outcomes=False)
  self.assertTrue(all(b['status']=='unconfirmed' for b in babies))
  self.assertFalse(any(t.get('template_key') in lifecycle.QUIET_TEMPLATE_KEYS for t in self.snap(self.mom)['tasks']))
  self.call(self.mom,'baby.update',self.data(babies[0],'growing'))
  with d.transaction() as c:preview=lifecycle.preview_baby(c,self.fid,self.mom,self.data(babies[1],'growing'))
  self.assertEqual(len(preview['created_tasks']),3)
  self.call(self.mom,'baby.update',self.data(babies[1],'growing'))
  self.assertEqual(sum(t.get('template_key') in lifecycle.QUIET_TEMPLATE_KEYS for t in self.snap(self.mom)['tasks']),3)

 def test_independent_birthday_survives_maternal_birth_date_correction(self):
  babies=self.prepare();birthday=str(d.today()-datetime.timedelta(days=4))
  self.call(self.mom,'baby.update',dict(self.data(babies[0],'growing'),birth_date=birthday))
  p=self.snap(self.mom)['pregnancy'];maternal=str(d.today()-datetime.timedelta(days=6))
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'correct_birth','date':maternal})
  babies=self.snap(self.mom)['babies'];self.assertEqual(babies[0]['birth_date'],birthday);self.assertEqual(babies[1]['birth_date'],maternal)

 def test_birth_confirmation_has_separate_permission_and_keeps_outcomes_out_of_maternal_history(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=100))})
  self.call(self.mom,'consent.update',{'scope':'health_edit','grantee':self.dad,'enabled':True})
  p=self.snap(self.mom)['pregnancy'];data={'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':2,'birth_outcomes':['hospitalized','lost'],'guardian_consent':True}
  for actor,changes in ((self.dad,{}),(self.mom,{'guardian_consent':False}),(self.mom,{'birth_outcomes':['growing']})):
   with self.assertRaises(d.Problem):self.call(actor,'pregnancy.event',dict(data,**changes))
  self.assertEqual(self.snap(self.mom)['pregnancy']['state'],'pregnant')
  result=self.call(self.mom,'pregnancy.event',data)
  self.assertEqual([b['status'] for b in self.snap(self.mom)['babies']],['hospitalized','lost'])
  with d.transaction() as c:
   event=d.get(c,'pregnancy_event',result['event_id']);self.assertNotIn('birth_outcomes',event['after']);self.assertEqual(len(d.rows(c,'baby_event',self.fid)),2)
  self.assertFalse(any(t.get('template_key') in lifecycle.QUIET_TEMPLATE_KEYS for t in self.snap(self.mom)['tasks']))

 def test_corrected_then_provisional_birth_can_generate_milestones_after_confirmation(self):
  self.prepare();p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'correct_birth_record','birth_event_id':p['birth_event_id'],'confirm_correction':True,'reason':'合成误录'})
  p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today())})
  baby=next(b for b in self.snap(self.mom)['babies'] if b['status']=='unconfirmed')
  with d.transaction() as c:preview=lifecycle.preview_baby(c,self.fid,self.mom,self.data(baby,'growing'))
  self.assertEqual(len(preview['restored_tasks']),3)
  self.call(self.mom,'baby.update',dict(self.data(baby,'growing'),preview_token=preview['preview_token']))
  self.assertTrue(all(t['status']!='cancelled' for t in self.snap(self.mom)['tasks'] if t.get('stage')=='postpartum'))
