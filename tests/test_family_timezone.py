import datetime
import unittest
from unittest.mock import patch
import test_domain
import domain as d
import dates


class FamilyTimezoneTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def change(self,**data):
  f=self.snap(self.dad)['family'];return self.call(self.dad,'family.update',dict(data,version=f['version']))
 def fixed_clock(self):
  original=dates.local_today;instant=datetime.datetime(2026,10,21,16,0,tzinfo=datetime.timezone.utc)
  return patch.object(dates,'local_today',side_effect=lambda timezone=dates.DEFAULT_TIMEZONE:original(timezone,instant))

 def test_explicit_timezone_is_stored_without_changing_dates(self):
  self.change(timezone='America/New_York')
  self.assertEqual(self.snap(self.dad)['family']['timezone'],'America/New_York')
  for value in ('Unknown/City','../etc/passwd','/etc/passwd','UTC+8',None):
   with self.assertRaises(d.Problem):self.change(timezone=value)
  self.assertEqual(self.snap(self.dad)['family']['timezone'],'America/New_York')

 def test_timezone_and_name_change_follow_current_approval_policy(self):
  self.change(mode='all');r=self.change(timezone='UTC',name='批准后的家庭名称')
  self.assertTrue(r['pending']);self.assertEqual(self.snap(self.dad)['family']['timezone'],'Asia/Shanghai')
  a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==r['approval_id'])
  self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  f=self.snap(self.dad)['family'];self.assertEqual(f['timezone'],'UTC');self.assertEqual(f['name'],'批准后的家庭名称');self.assertEqual(f['mode'],'all')

 def test_timezone_change_cancels_old_reminder_and_uses_new_day_window(self):
  with self.fixed_clock():
   task=self.call(self.dad,'task.create',{'title':'时区测试','due_date':'2026-10-23'})['id'];d.tick()
   self.assertEqual(sum(not n.get('cancelled') for n in self.snap(self.dad)['notifications']),1)
   self.change(timezone='UTC');d.tick()
   self.assertFalse(any(not n.get('cancelled') for n in self.snap(self.dad)['notifications']))
   self.assertEqual(next(t for t in self.snap(self.dad)['tasks'] if t['id']==task)['due_date'],'2026-10-23')

 def test_new_timezone_cannot_make_recorded_birth_a_future_date(self):
  with self.fixed_clock():
   self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
   self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':'2027-01-01'});p=self.snap(self.mom)['pregnancy']
   self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':'2026-10-22'})
   with self.assertRaises(d.Problem) as err:self.change(timezone='UTC')
   self.assertEqual(err.exception.status,409);self.assertEqual(self.snap(self.dad)['family']['timezone'],'Asia/Shanghai')

 def test_timezone_is_revalidated_when_mother_approves(self):
  with self.fixed_clock():
   self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
   self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':'2027-01-01'})
   self.change(mode='all');r=self.change(timezone='UTC')
   p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':'2026-10-22'})
   a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==r['approval_id'])
   with self.assertRaises(d.Problem):self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
   self.assertEqual(self.snap(self.dad)['family']['timezone'],'Asia/Shanghai')

 def test_pregnancy_timeline_changes_calendar_day_without_rewriting_birth(self):
  with self.fixed_clock():
   self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
   self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':'2027-01-01'});p=self.snap(self.mom)['pregnancy']
   self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':'2026-10-21'})
   self.assertEqual(self.snap(self.mom)['pregnancy']['timeline']['postpartum_days'],1)
   self.change(timezone='UTC')
   p=self.snap(self.mom)['pregnancy'];self.assertEqual(p['birth_date'],'2026-10-21');self.assertEqual(p['timeline']['postpartum_days'],0)

 def test_non_coordinator_and_stale_updates_are_rejected(self):
  f=self.snap(self.dad)['family']
  with self.assertRaises(d.Problem):self.call(self.mom,'family.update',{'version':f['version'],'timezone':'UTC'})
  self.change(timezone='UTC')
  with self.assertRaises(d.Problem) as err:self.call(self.dad,'family.update',{'version':f['version'],'timezone':'Asia/Tokyo'})
  self.assertEqual(err.exception.status,409)

 def test_timezone_cannot_make_independent_child_status_date_future(self):
  with self.fixed_clock():
   self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
   self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':'2027-01-01'});p=self.snap(self.mom)['pregnancy']
   self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':'2026-10-21'})
   b=self.snap(self.mom)['babies'][0];self.call(self.mom,'baby.update',{'id':b['id'],'version':b['version'],'status':'hospitalized','date':'2026-10-22','guardian_consent':True})
   with self.assertRaises(d.Problem):self.change(timezone='UTC')
   self.assertEqual(self.snap(self.dad)['family']['timezone'],'Asia/Shanghai')
