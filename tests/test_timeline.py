import datetime
import unittest
import test_domain
import domain as d


class TimelineTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def pregnancy(self):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=100))})
  return self.snap(self.mom)['pregnancy']

 def preview(self,data):
  with d.transaction() as c:return d.preview_transition(c,self.mom,self.fid,data)

 def test_preview_is_read_only_and_matches_committed_reschedule(self):
  p=self.pregnancy();t=next(x for x in self.snap(self.dad)['tasks'] if x.get('template_key'))
  self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  self.call(self.mom,'appointment.create',{'title':'已约机构时间','date':str(d.today()+datetime.timedelta(days=2))})
  data={'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.date(p['edd'])+datetime.timedelta(days=5))}
  def stored():
   with d.transaction() as c:return ([tuple(x) for x in c.execute('SELECT * FROM records ORDER BY kind,id')],[tuple(x) for x in c.execute('SELECT * FROM events ORDER BY id')])
  before=stored();preview=self.preview(data);self.assertEqual(before,stored())
  self.assertGreater(preview['counts']['rescheduled'],0)
  self.assertEqual(preview['preserved_completed'],1);self.assertEqual(preview['pinned_appointments'],1)
  self.call(self.mom,'pregnancy.event',dict(data,preview_token=preview['preview_token']))
  tasks={x['id']:x for x in self.snap(self.mom)['tasks']}
  for change in preview['changes']:self.assertEqual(tasks[change['id']]['due_date'],change['after_date'])
  self.assertEqual(tasks[t['id']]['due_date'],t['due_date'])

 def test_preview_stales_when_task_changes(self):
  p=self.pregnancy();data={'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.date(p['edd'])+datetime.timedelta(days=5))}
  preview=self.preview(data);t=self.snap(self.dad)['tasks'][0]
  self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'title':'并发修改'})
  with self.assertRaises(d.Problem) as err:self.call(self.mom,'pregnancy.event',dict(data,preview_token=preview['preview_token']))
  self.assertEqual(err.exception.status,409);self.assertEqual(self.snap(self.mom)['pregnancy']['edd'],p['edd'])

 def test_birth_preview_does_not_create_babies_until_confirmation(self):
  p=self.pregnancy();data={'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':2}
  preview=self.preview(data)
  with d.transaction() as c:self.assertEqual(d.rows(c,'baby',self.fid),[])
  self.assertGreater(preview['counts']['cancelled'],0)
  self.call(self.mom,'pregnancy.event',dict(data,preview_token=preview['preview_token']))
  with d.transaction() as c:self.assertEqual(len(d.rows(c,'baby',self.fid)),2)

 def test_preview_requires_current_view_and_edit_grants(self):
  p=self.pregnancy();data={'id':p['id'],'version':p['version'],'event':'pause'}
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_edit','enabled':True})
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_read','enabled':False})
  with self.assertRaises(d.Problem) as err:
   with d.transaction() as c:d.preview_transition(c,self.dad,self.fid,data)
  self.assertEqual(err.exception.status,403)
  self.assertNotIn('timeline',self.snap(self.dad)['pregnancy'])

 def test_redating_cancels_old_task_reminder(self):
  p=self.pregnancy();t=next(x for x in self.snap(self.mom)['tasks'] if x.get('template_key'))
  with d.transaction() as c:d.put(c,'notification',{'id':'old-reminder','ref':t['id'],'recipient':self.mom,'pregnancy_id':p['id'],'read':False,'created_at':d.now()},self.fid)
  data={'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.date(p['edd'])+datetime.timedelta(days=3))}
  preview=self.preview(data);self.assertEqual(preview['cancelled_notifications'],1)
  self.call(self.mom,'pregnancy.event',dict(data,preview_token=preview['preview_token']))
  with d.transaction() as c:self.assertTrue(d.get(c,'notification','old-reminder')['cancelled'])

 def test_invalid_source_and_non_iso_dates_are_rejected(self):
  p=self.pregnancy()
  for date,source in ((p['edd'],'invented'),(p['edd'].replace('-',''),'user_entered')):
   with self.assertRaises(d.Problem) as err:self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':date,'source':source})
   self.assertEqual(err.exception.status,400)

 def test_lmp_preview_records_original_date_and_estimated_edd(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
  data={'id':p['id'],'version':p['version'],'event':'set_lmp','date':str(d.today()),'reason':'本人首次录入'}
  preview=self.preview(data)
  self.assertEqual(preview['effective_edd'],str(d.today()+datetime.timedelta(days=280)))
  self.assertEqual(preview['dating_source'],'lmp_estimate')
  self.assertIsNone(self.snap(self.mom)['pregnancy']['edd'])
  self.call(self.mom,'pregnancy.event',dict(data,preview_token=preview['preview_token']))
  result=self.snap(self.mom)['pregnancy']
  self.assertEqual(result['lmp'],data['date']);self.assertEqual(result['timeline']['gestational_days'],0)
  with d.transaction() as c:
   event=c.execute("SELECT body FROM events WHERE type='health.transition'").fetchone()
   body=d.json.loads(event['body']);self.assertEqual(body['reason'],data['reason'])
   self.assertEqual(body['effective_date'],preview['effective_edd']);self.assertEqual(body['revision'],2)

 def test_lmp_cannot_replace_clinician_date_even_via_legacy_source(self):
  p=self.pregnancy()
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':p['edd'],'source':'clinician_entered'})
  p=self.snap(self.mom)['pregnancy']
  for event,date in (('set_lmp',str(d.today()-datetime.timedelta(days=100))),('set_edd',p['edd'])):
   with self.assertRaises(d.Problem) as err:self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':event,'date':date,'source':'lmp_estimate'})
   self.assertEqual(err.exception.status,409)
  self.assertEqual(self.snap(self.mom)['pregnancy']['dating_source'],'clinician_entered')

 def test_invalid_lmp_is_rejected_and_deletion_removes_dated_audit(self):
  p=self.pregnancy()
  for value in (str(d.today()+datetime.timedelta(days=1)),str(d.today()-datetime.timedelta(days=400))):
   with self.assertRaises(d.Problem) as err:self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_lmp','date':value})
   self.assertEqual(err.exception.status,400)
  with d.transaction() as c:
   d.delete_account(c,self.mom,'long-password-123')
   self.assertEqual(c.execute("SELECT count(*) FROM events WHERE type LIKE 'health.%'").fetchone()[0],0)
