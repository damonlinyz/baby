import contextlib
import datetime as dt
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_domain
import domain as d


@contextlib.contextmanager
def clock(value):
 instant=dt.datetime.fromisoformat(value)
 with patch.object(d,'now',return_value=int(instant.timestamp())),patch.object(d,'today',side_effect=lambda timezone='Asia/Shanghai':instant.astimezone(ZoneInfo(timezone)).date()):yield


class ReminderPreferenceTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def save(self,user=None,**data):return self.call(user or self.dad,'notification.preferences',data)
 def task(self,date):return self.call(self.dad,'task.create',{'title':'合成任务','due_date':date})['id']
 def queue(self):
  with d.transaction() as c:return d.rows(c,'reminder',self.fid)
 def notices(self,user=None):return [n for n in self.snap(user or self.dad)['notifications'] if n.get('managed_reminder')]

 def test_overnight_quiet_hours_defer_until_end_without_duplicate_delivery(self):
  self.save(quiet_enabled=True,quiet_start='22:00',quiet_end='08:00')
  with clock('2026-11-02T23:00:00+08:00'):self.task('2026-11-03');d.tick();self.assertEqual(self.notices(),[]);self.assertEqual(self.queue()[0]['status'],'scheduled')
  with clock('2026-11-03T07:59:00+08:00'):d.tick();self.assertEqual(self.notices(),[])
  with clock('2026-11-03T08:00:00+08:00'):d.tick();d.tick();self.assertEqual(len(self.notices()),1);self.assertEqual(self.queue()[0]['attempts'],1)

 def test_daily_limit_and_merge_cap_count_messages_not_entries(self):
  self.save(daily_limit=1,merge_enabled=True,merge_limit=2)
  with clock('2026-11-02T09:00:00+08:00'):
   for _ in range(3):self.task('2026-11-03')
   d.tick();self.assertEqual(len(self.notices()),1);self.assertEqual(len(self.notices()[0]['delivery_refs']),2);self.assertEqual(sum(q['status']=='scheduled' for q in self.queue()),1)
   d.tick();self.assertEqual(len(self.notices()),1)
  with clock('2026-11-03T09:00:00+08:00'):d.tick();self.assertEqual(len(self.notices()),2)

 def test_expired_quiet_reminder_is_never_backfilled(self):
  self.save(quiet_enabled=True,quiet_start='00:00',quiet_end='23:59')
  with clock('2026-11-02T10:00:00+08:00'):self.task('2026-11-02');d.tick();self.assertEqual(self.notices(),[])
  with clock('2026-11-03T23:59:00+08:00'):d.tick();self.assertEqual(self.notices(),[]);self.assertEqual(self.queue()[0]['status'],'expired')

 def test_reschedule_cancel_and_preference_off_cancel_queued_work(self):
  self.save(quiet_enabled=True,quiet_start='00:00',quiet_end='23:59')
  with clock('2026-11-02T10:00:00+08:00'):
   id=self.task('2026-11-03');d.tick();t=next(t for t in self.snap(self.dad)['tasks'] if t['id']==id)
   self.call(self.dad,'task.update',{'id':id,'version':t['version'],'due_date':'2026-11-04'});self.assertEqual(self.queue()[0]['status'],'cancelled')
   self.save(task_days=[2]);d.tick();self.assertEqual(sum(q['status']=='scheduled' for q in self.queue()),1)
   self.save(enabled=False);self.assertTrue(all(q['status']=='cancelled' for q in self.queue()))

 def test_custom_appointment_leads_create_one_message_at_each_new_window(self):
  self.save(self.mom,appointment_days=[3,1,0])
  with clock('2026-11-02T09:00:00+08:00'):
   self.call(self.mom,'pregnancy.create',{'consent':True});self.call(self.mom,'appointment.create',{'title':'预约','date':'2026-11-04'});d.tick();self.assertEqual(len(self.notices(self.mom)),1)
  with clock('2026-11-03T09:00:00+08:00'):d.tick();self.assertEqual(len(self.notices(self.mom)),2)
  with clock('2026-11-04T09:00:00+08:00'):d.tick();d.tick();self.assertEqual(len(self.notices(self.mom)),3)

 def test_current_closest_lead_supersedes_undelivered_earlier_window(self):
  self.save(self.mom,appointment_days=[3,1,0],quiet_enabled=True,quiet_start='00:00',quiet_end='23:59')
  with clock('2026-11-02T09:00:00+08:00'):
   self.call(self.mom,'pregnancy.create',{'consent':True});self.call(self.mom,'appointment.create',{'title':'预约','date':'2026-11-04'});d.tick()
  with clock('2026-11-03T23:59:00+08:00'):d.tick();self.assertEqual(len(self.notices(self.mom)),1);self.assertEqual(sum(q['status']=='cancelled' for q in self.queue()),1)

 def test_pending_approval_respects_quiet_and_cannot_deliver_after_invalidation(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  self.save(self.mom,quiet_enabled=True,quiet_start='22:00',quiet_end='08:00')
  with clock('2026-11-02T23:00:00+08:00'):
   t=self.snap(self.dad)['tasks'][0];self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'});self.assertEqual(self.notices(self.mom),[])
   t=next(record for record in self.snap(self.dad)['tasks'] if record['id']==t['id'])
   self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'note':'新说明'})
  with clock('2026-11-03T08:00:00+08:00'):d.tick();self.assertEqual(self.notices(self.mom),[]);self.assertTrue(all(q['status']=='cancelled' for q in self.queue()))

 def test_delayed_health_reminder_rechecks_revoked_access(self):
  with clock('2026-11-02T23:00:00+08:00'):
   self.call(self.mom,'pregnancy.create',{'consent':True})
   for scope in ('health_read','health_edit'):self.call(self.mom,'consent.update',{'scope':scope,'grantee':self.dad,'enabled':True})
   self.save(quiet_enabled=True,quiet_start='22:00',quiet_end='08:00');self.call(self.dad,'appointment.create',{'title':'私密预约','date':'2026-11-03'});d.tick()
   self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':False})
  with clock('2026-11-03T08:00:00+08:00'):d.tick();self.assertEqual(self.notices(),[]);self.assertTrue(all(q['status']=='cancelled' for q in self.queue()))

 def test_invalid_preferences_are_atomic_and_partial_changes_preserve_other_settings(self):
  before=self.snap(self.dad)['reminder_preferences']
  for data in ({'enabled':'false'},{'daily_limit':True},{'daily_limit':0},{'merge_limit':21},{'quiet_start':'24:00'},{'quiet_enabled':True,'quiet_start':'08:00','quiet_end':'08:00'},{'appointment_days':[1,1]},{'task_days':[8]},{'appointment_days':['1']}):
   with self.assertRaises(d.Problem):self.save(**data)
  self.assertEqual(self.snap(self.dad)['reminder_preferences'],before)
  self.save(daily_limit=3);self.save(enabled=False);self.assertEqual(self.snap(self.dad)['reminder_preferences']['daily_limit'],3)

 def test_upgrade_never_reopens_an_already_delivered_legacy_notice(self):
  with clock('2026-11-02T09:00:00+08:00'):
   id=self.task('2026-11-02');notice_id='task-'+id+'-2026-11-02'
   with d.transaction() as c:d.put(c,'notification',{'id':notice_id,'ref':id,'recipient':self.dad,'title':'原有提醒','read':True,'created_at':d.now()-60},self.fid)
   d.tick()
   with d.transaction() as c:n=d.get(c,'notification',notice_id);self.assertTrue(n['read']);self.assertEqual(n['title'],'原有提醒');self.assertEqual(d.rows(c,'reminder',self.fid),[])
