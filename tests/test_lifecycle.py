import datetime
import unittest
import test_domain
import domain as d


class BirthCorrectionTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def prepare(self):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=100))})
  tasks=[t for t in self.snap(self.dad)['tasks'] if t.get('template_key')]
  self.done,self.manual=tasks[:2]
  for task,status in ((self.done,'done'),(self.manual,'cancelled')):self.call(self.dad,'task.update',{'id':task['id'],'version':task['version'],'status':status})
  self.call(self.mom,'appointment.create',{'title':'手动预约','date':str(d.today()+datetime.timedelta(days=3))})
  p=self.snap(self.mom)['pregnancy']
  self.original=self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':2,'birth_outcomes':['growing','growing'],'guardian_consent':True})['event_id']
  return self.snap(self.mom)['pregnancy']

 def correction(self,p):return {'id':p['id'],'version':p['version'],'event':'correct_birth_record','birth_event_id':self.original,'confirm_correction':True,'reason':'合成数据：误触分娩记录'}

 def test_preview_and_compensation_preserve_original_history(self):
  p=self.prepare();data=self.correction(p)
  with d.transaction() as c:
   original=d.get(c,'pregnancy_event',self.original)
   before=[tuple(r) for r in c.execute('SELECT * FROM records ORDER BY kind,id')]
   preview=d.preview_transition(c,self.mom,self.fid,data)
   self.assertEqual(before,[tuple(r) for r in c.execute('SELECT * FROM records ORDER BY kind,id')])
  self.assertGreater(preview['counts']['restored'],0);self.assertGreater(preview['counts']['cancelled'],0);self.assertTrue(preview['paused'])
  result=self.call(self.mom,'pregnancy.event',dict(data,preview_token=preview['preview_token']))
  s=self.snap(self.mom);self.assertEqual(s['pregnancy']['state'],'pregnant');self.assertTrue(s['pregnancy']['paused']);self.assertIsNone(s['pregnancy']['birth_date'])
  self.assertEqual(len(s['appointments']),1)
  tasks={t['id']:t for t in s['tasks']}
  self.assertEqual(tasks[self.done['id']]['status'],'done');self.assertEqual(tasks[self.done['id']]['due_date'],self.done['due_date'])
  self.assertEqual(tasks[self.manual['id']]['status'],'cancelled')
  with d.transaction() as c:
   self.assertEqual(d.get(c,'pregnancy_event',self.original),original)
   babies=d.rows(c,'baby',self.fid);self.assertEqual(len(babies),2)
   self.assertTrue(all(b['status']=='entry_corrected' and b['corrected_by_event']==result['event_id'] for b in babies))
  d.tick()
  self.assertFalse(any(not n.get('cancelled') and n.get('pregnancy_id')==p['id'] for n in self.snap(self.mom)['notifications']))

 def test_correction_requires_subject_confirmation_reason_and_original_event(self):
  p=self.prepare();self.call(self.mom,'consent.update',{'scope':'health_edit','grantee':self.dad,'enabled':True})
  with self.assertRaises(d.Problem) as err:self.call(self.dad,'pregnancy.event',self.correction(p))
  self.assertEqual(err.exception.status,403)
  for changes in ({'confirm_correction':False},{'reason':''},{'birth_event_id':'wrong-event'}):
   with self.assertRaises(d.Problem):self.call(self.mom,'pregnancy.event',dict(self.correction(p),**changes))
  self.assertEqual(self.snap(self.mom)['pregnancy']['state'],'postpartum')

 def test_recording_birth_again_retains_corrected_babies_and_uses_new_ids(self):
  p=self.prepare();self.call(self.mom,'pregnancy.event',self.correction(p));p=self.snap(self.mom)['pregnancy']
  new=self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':2,'birth_outcomes':['growing','growing'],'guardian_consent':True})
  self.assertNotEqual(new['event_id'],self.original)
  self.assertTrue(all(t['status']!='cancelled' for t in self.snap(self.mom)['tasks'] if t.get('stage')=='postpartum'))
  with d.transaction() as c:
   babies=d.rows(c,'baby',self.fid);self.assertEqual(len(babies),4)
   self.assertEqual(sum(b['status']=='growing' for b in babies),2)
  with self.assertRaises(d.Problem):self.call(self.mom,'pregnancy.event',self.correction(self.snap(self.mom)['pregnancy']))

 def test_later_child_status_prevents_reclassifying_birth_as_mistake(self):
  p=self.prepare()
  with d.transaction() as c:
   baby=d.rows(c,'baby',self.fid)[0];baby['status']='hospitalized';d.put(c,'baby',baby,self.fid)
  with self.assertRaises(d.Problem) as err:self.call(self.mom,'pregnancy.event',self.correction(p))
  self.assertEqual(err.exception.status,409)

 def test_mistaken_birth_correction_preserves_custom_household_tasks(self):
  p=self.prepare();task_id=self.call(self.dad,'task.create',{'title':'每日清理厨房','due_date':str(d.today())})['id']
  self.call(self.mom,'pregnancy.event',self.correction(p))
  task=next(t for t in self.snap(self.dad)['tasks'] if t['id']==task_id)
  self.assertEqual(task['status'],'todo');self.assertEqual(task['due_date'],str(d.today()))
