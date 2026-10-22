import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import dates


class CalendarTests(unittest.TestCase):
 def test_pregnancy_boundaries_and_overdue(self):
  current=dt.date(2026,10,22)
  for days,phase in ((0,'early'),(97,'early'),(98,'middle'),(195,'middle'),(196,'late'),(280,'late'),(301,'late')):
   with self.subTest(days=days):
    p={'state':'pregnant','edd':str(current+dt.timedelta(days=280-days))}
    result=dates.timeline(p,on_date=current)
    self.assertEqual((result['weeks'],result['days']),divmod(days,7))
    self.assertEqual(result['phase'],phase);self.assertEqual(p['state'],'pregnant')
    if days>=280:self.assertIn('不会自动切换',result['description'])
 def test_negative_gestational_age_is_rejected(self):
  with self.assertRaises(ValueError):dates.timeline({'state':'pregnant','edd':'2027-08-01'},on_date=dt.date(2026,10,22))
 def test_family_midnight_does_not_follow_device_timezone(self):
  instant=dt.datetime(2026,10,21,16,0,tzinfo=dt.timezone.utc)
  self.assertEqual(str(dates.local_today('Asia/Shanghai',instant)),'2026-10-22')
  self.assertEqual(str(dates.local_today('America/New_York',instant)),'2026-10-21')
  self.assertEqual(str(dates.local_today('Asia/Shanghai',instant-dt.timedelta(seconds=1))),'2026-10-21')
 def test_daylight_saving_uses_calendar_dates(self):
  before=dt.datetime(2026,3,8,5,tzinfo=dt.timezone.utc)
  after=dt.datetime(2026,3,9,4,tzinfo=dt.timezone.utc)
  self.assertEqual((dates.local_today('America/New_York',after)-dates.local_today('America/New_York',before)).days,1)
 def test_postpartum_day_zero_and_42_43(self):
  birth=dt.date(2026,1,1)
  for days,phase in ((0,'postpartum_early'),(42,'postpartum_early'),(43,'postpartum_later')):
   with self.subTest(days=days):
    r=dates.timeline({'state':'postpartum','birth_date':str(birth)},on_date=birth+dt.timedelta(days=days))
    self.assertEqual(r['postpartum_days'],days);self.assertEqual(r['phase'],phase)
 def test_first_birthday_is_calendar_anniversary(self):
  birth=dt.date(2023,3,1)
  self.assertFalse(dates.age(birth,dt.date(2024,2,29))['reached_first_birthday'])
  self.assertTrue(dates.age(birth,dt.date(2024,3,1))['reached_first_birthday'])
  leap=dt.date(2024,2,29)
  self.assertEqual(str(dates.anniversary(leap)),'2025-02-28')
  self.assertEqual(dates.age(leap,dt.date(2025,2,28))['months'],12)
 def test_strict_date_format_and_future_birth(self):
  for value in ('20261022','2026-02-30','2026-1-1',None):
   with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):dates.parse(value)
  with self.assertRaises(ValueError):dates.age(dt.date(2026,1,2),dt.date(2026,1,1))
 def test_unknown_and_ended_dates_are_not_inferred(self):
  self.assertEqual(dates.timeline({'state':'dating'})['phase'],'dating')
  self.assertEqual(dates.timeline({'state':'ended','edd':'2026-10-22'})['phase'],'ended')
