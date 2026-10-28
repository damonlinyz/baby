import datetime
import unittest
from unittest.mock import patch

import test_domain
import domain as d
import calendar_export as calendar


class CalendarTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def export(self,user=None,start=None,end=None):
  with d.transaction() as c:return calendar.export(c,user or self.dad,self.fid,start,end)
 def task(self,title='导出家务',**changes):
  return self.call(self.dad,'task.create',dict({'title':title,'due_date':str(d.today()),'note':'不要导出的私密备注'},**changes))['id']
 def set_status(self,id,status,user=None):
  task=next(t for t in self.snap(user or self.dad)['tasks'] if t['id']==id)
  self.call(user or self.dad,'task.update',{'id':id,'version':task['version'],'status':status})
 def raw(self,kind,obj):
  with d.transaction() as c:return d.put(c,kind,dict({'id':d.uid()},**obj),self.fid)

 def test_all_day_events_use_exclusive_end_and_include_range_boundaries(self):
  for due in ('2026-12-31','2027-01-01','2027-01-02'):self.task(due,due_date=due)
  result=self.export(start='2026-12-31',end='2027-01-01');text=result['content']
  self.assertEqual(result['event_count'],2);self.assertIn('DTSTART;VALUE=DATE:20261231\r\nDTEND;VALUE=DATE:20270101',text)
  self.assertIn('DTSTART;VALUE=DATE:20270101\r\nDTEND;VALUE=DATE:20270102',text)
  self.assertNotIn('2027-01-02',text);self.assertNotIn('不要导出的私密备注',text)
  self.assertTrue(text.endswith('END:VCALENDAR\r\n'));self.assertEqual(text.count('BEGIN:VEVENT'),2)

 def test_no_date_or_closed_tasks_and_cancelled_appointments_are_excluded(self):
  for status in ('done','skipped','cancelled'):
   id=self.task(status);self.set_status(id,status)
  self.task('没有日期',due_date=None);self.task('进行中');self.raw('appointment',{'title':'有效预约','date':str(d.today()),'status':'scheduled','owner':self.mom,'pregnancy_id':None})
  self.raw('appointment',{'title':'我的有效预约','date':str(d.today()),'status':'scheduled','owner':self.dad,'pregnancy_id':None})
  self.raw('appointment',{'title':'取消预约','date':str(d.today()),'status':'cancelled','owner':self.dad,'pregnancy_id':None})
  result=self.export();self.assertEqual(result['event_count'],2);self.assertIn('我的有效预约',result['content']);self.assertNotIn('SUMMARY:[预约] 有效预约',result['content'])

 def test_pending_tasks_export_without_approval_or_people_metadata(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  id=self.task();self.set_status(id,'done');text=self.export()['content'];self.assertIn('导出家务',text)
  for forbidden in ('ATTENDEE','ORGANIZER','VALARM','DESCRIPTION',self.dad,self.mom):self.assertNotIn(forbidden,text)
  self.assertIn('CLASS:PRIVATE',text)

 def test_utf8_octet_folding_and_escaping_prevent_property_injection(self):
  title='多字节物品'*10+'\\,;\r\nEND:VEVENT\nBEGIN:VEVENT\nSUMMARY:注入'
  self.task(title);text=self.export()['content'];lines=text.split('\r\n')
  self.assertTrue(all(len(line.encode('utf8'))<=75 for line in lines));self.assertTrue(any(line.startswith(' ') for line in lines))
  self.assertEqual(text.count('\r\nBEGIN:VEVENT\r\n'),1)
  unfolded=text.replace('\r\n ','');self.assertIn('\\\\\\,\\;',unfolded);self.assertIn('\\nEND:VEVENT\\nBEGIN:VEVENT\\nSUMMARY:注入',unfolded)

 def test_control_characters_are_removed_and_crlf_is_one_escaped_newline(self):
  self.task('事项\x00\x1b\x85\r\n第二行')
  text=self.export()['content'];self.assertIn('事项\\n第二行',text)
  for char in ('\x00','\x1b','\x85'):self.assertNotIn(char,text)

 def test_stable_uid_version_sequence_and_no_export_side_effects_on_resources(self):
  id=self.task();before=self.snap(self.dad)['tasks'];first=self.export()['content'];second=self.export()['content']
  uid=lambda text:next(x for x in text.split('\r\n') if x.startswith('UID:'))
  self.assertEqual(uid(first),uid(second));self.assertNotIn(id,uid(first));self.assertEqual(before,self.snap(self.dad)['tasks'])
  task=next(t for t in before if t['id']==id);self.call(self.dad,'task.update',{'id':id,'version':task['version'],'due_date':str(d.today()+datetime.timedelta(days=1))})
  third=self.export()['content'];self.assertEqual(uid(first),uid(third));self.assertIn('SEQUENCE:'+str(task['version']+1),third)

 def test_basic_member_export_cannot_include_others_tasks(self):
  invite=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,invite['invite_code'])
  self.task('我的任务',assignee=self.other);self.task('别人的任务');text=self.export(self.other)['content']
  self.assertIn('我的任务',text);self.assertNotIn('别人的任务',text)
  self.call(self.dad,'member.remove',{'user_id':self.other})
  with self.assertRaises(d.Problem):self.export(self.other)

 def test_health_revocation_immediately_filters_appointments_and_sensitive_tasks(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'appointment.create',{'title':'私密预约','date':str(d.today())})
  self.raw('task',{'title':'敏感日期待办','note':'','assignee':self.dad,'status':'todo','due_date':str(d.today()),'pregnancy_id':p['id'],'private_health_anchor':True})
  self.assertNotIn('私密预约',self.export()['content']);self.assertNotIn('敏感日期待办',self.export()['content'])
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  text=self.export()['content'];self.assertIn('私密预约',text);self.assertIn('敏感日期待办',text)
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':False})
  text=self.export()['content'];self.assertNotIn('私密预约',text);self.assertNotIn('敏感日期待办',text)

 def test_baby_tasks_require_independent_baby_consent(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=1))});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':1,'guardian_consent':True,'birth_outcomes':['growing']})
  import lifecycle
  self.raw('task',{'title':'宝宝专属待办','note':'','assignee':self.dad,'status':'todo','due_date':str(d.today()),'pregnancy_id':p['id'],'template_key':next(iter(lifecycle.BABY_TEMPLATE_KEYS))})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True});self.assertNotIn('宝宝专属待办',self.export()['content'])
  self.call(self.mom,'consent.update',{'scope':'baby_read','grantee':self.dad,'enabled':True,'guardian_consent':True});self.assertIn('宝宝专属待办',self.export()['content'])
  self.call(self.mom,'consent.update',{'scope':'baby_read','grantee':self.dad,'enabled':False});self.assertNotIn('宝宝专属待办',self.export()['content'])

 def test_recurring_export_uses_materialized_instances_without_recurrence_rule(self):
  self.call(self.dad,'task.create',{'title':'按天安排','repeat':'daily','due_date':str(d.today())})
  text=self.export()['content'];self.assertEqual(text.count('BEGIN:VEVENT'),15);self.assertNotIn('RRULE',text)

 def test_family_day_defaults_and_dst_and_leap_days_are_calendar_dates(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'timezone':'America/New_York'})
  self.task('夏令时当天',due_date='2026-03-08');self.task('闰日',due_date='2028-02-29')
  with patch.object(d,'today',return_value=datetime.date(2026,3,8)):
   result=self.export();self.assertEqual(result['start'],'2026-03-08');self.assertIn('DTEND;VALUE=DATE:20260309',result['content'])
  self.assertIn('DTEND;VALUE=DATE:20280301',self.export(start='2028-02-29',end='2028-02-29')['content'])

 def test_invalid_date_ranges_and_excessive_windows_reject_without_partial_output(self):
  for start,end in (('bad','2026-10-28'),('2026-10-29','2026-10-28'),('2026-01-01','2027-01-02'),('9999-12-31',None),('9999-12-30','9999-12-31')):
   with self.subTest(start=start,end=end):
    with self.assertRaises(d.Problem):self.export(start=start,end=end)
  result=self.export(start='2026-01-01',end='2027-01-01');self.assertEqual(result['event_count'],0)
