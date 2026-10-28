import datetime as dt
import json
import unittest
from unittest.mock import patch

import test_domain
import domain as d
import reports


def stamp(value):
 return int(dt.datetime.fromisoformat(value).timestamp())


class ReportTests(unittest.TestCase):
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def setUp(self):
  self.day=patch.object(d,'today',return_value=dt.date(2026,10,28));self.day.start()
  self.clock=patch.object(d,'now',return_value=stamp('2026-10-28T12:00:00+00:00'));self.clock.start()
  self.addCleanup(self.clock.stop);self.addCleanup(self.day.stop)
  test_domain.DomainTests.setUp(self)
  with d.transaction() as c:d.seed_knowledge(c)
 def tearDown(self):test_domain.DomainTests.tearDown(self)
 def report(self,user=None,week=None):return reports.build(self.snap(user or self.dad),week)
 def raw(self,kind='task',**changes):
  obj={'id':d.uid(),'title':'合成完成事实','name':'合成物品','note':'不应出现在报告的备注','quantity':2,'level':'建议','pack':'妈妈入院包','assignee':self.dad,'status':'done','due_date':None,'completed_at':stamp('2026-10-27T12:00:00+08:00')}
  obj.update(changes)
  with d.transaction() as c:return d.put(c,kind,obj,self.fid)
 def clear_open(self):
  with d.transaction() as c:
   for kind in ('task','item'):
    for obj in d.rows(c,kind,self.fid):obj['status']='cancelled';d.put(c,kind,obj,self.fid)
 def grant(self,scope,enabled=True):self.call(self.mom,'consent.update',{'scope':scope,'grantee':self.dad,'enabled':enabled,'guardian_consent':scope=='baby_read' and enabled})
 def pregnancy(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});return self.snap(self.mom)['pregnancy']

 def test_current_report_has_natural_week_bounds_and_visible_facts_only(self):
  self.raw(title='本周完成');self.raw('item',name='本周物品',quantity=5);self.raw(title='上周完成',completed_at=stamp('2026-10-25T12:00:00+08:00'))
  r=self.report();self.assertEqual((r['week_start'],r['week_end']),('2026-10-26','2026-11-01'))
  self.assertEqual(r['counts'],{'tasks':1,'items':1});self.assertEqual(r['completed']['items'][0]['quantity'],5)
  self.assertEqual(r['completed']['tasks'][0]['title'],'本周完成');self.assertNotIn('不应出现在报告的备注',json.dumps(r,ensure_ascii=False))
  self.assertIsNone(r['next_week']);self.assertEqual(r['previous_week'],'2026-10-19')
  self.assertEqual(self.snap(self.dad)['weekly_report'],r)

 def test_local_monday_boundary_is_inclusive_and_next_monday_is_excluded(self):
  for title,completed in (('周一零点','2026-10-25T16:00:00+00:00'),('此前一秒','2026-10-25T15:59:59+00:00'),('周日最后一秒','2026-11-01T15:59:59+00:00'),('下周零点','2026-11-01T16:00:00+00:00')):
   self.raw(title=title,completed_at=stamp(completed))
  with patch.object(d,'today',return_value=dt.date(2026,11,2)),patch.object(d,'now',return_value=stamp('2026-11-02T12:00:00+00:00')):
   r=self.report(week='2026-10-28')
  self.assertEqual({t['title'] for t in r['completed']['tasks']},{'周一零点','周日最后一秒'})
  self.assertFalse(r['is_current']);self.assertEqual(r['next_week'],'2026-11-02');self.assertFalse(r['briefing']['enabled'])

 def test_dst_repeated_hour_and_calendar_year_boundary_are_not_fixed_utc_weeks(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'timezone':'America/New_York'})
  for value in ('2026-11-01T05:30:00+00:00','2026-11-01T06:30:00+00:00','2026-11-02T04:59:59+00:00','2026-11-02T05:00:00+00:00'):
   self.raw(title=value,completed_at=stamp(value))
  with patch.object(d,'today',return_value=dt.date(2026,11,3)),patch.object(d,'now',return_value=stamp('2026-11-03T12:00:00+00:00')):
   r=self.report(week='2026-11-01')
  self.assertEqual(r['counts']['tasks'],3);self.assertEqual(r['timezone'],'America/New_York')
  self.raw(completed_at=stamp('2021-01-03T23:59:59-05:00'))
  r=self.report(week='2021-01-01');self.assertEqual((r['week_start'],r['week_end']),('2020-12-28','2021-01-03'));self.assertEqual(r['counts']['tasks'],1)

 def test_invalid_or_future_weeks_reject_and_earliest_calendar_week_has_no_previous(self):
  for week in ('bad','2026-2-1',True,'2026-11-02','9999-12-31'):
   with self.subTest(week=week):
    with self.assertRaises(d.Problem):self.report(week=week)
  r=self.report(week='0001-01-01');self.assertIsNone(r['previous_week']);self.assertEqual(r['counts'],{'tasks':0,'items':0})
  self.assertEqual(self.report(week='2026-11-01')['week_start'],'2026-10-26')

 def test_approval_counts_on_confirmation_week_and_not_submission_week(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  t=self.snap(self.dad)['tasks'][0]
  with patch.object(d,'now',return_value=stamp('2026-10-25T12:00:00+08:00')):
   result=self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  with patch.object(d,'now',return_value=stamp('2026-10-26T12:00:00+08:00')):
   self.assertEqual(self.report(week='2026-10-25')['counts']['tasks'],0);self.assertEqual(self.report()['counts']['tasks'],0)
   a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==result['approval_id'])
   self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
   self.assertEqual(self.report()['counts']['tasks'],1);self.assertEqual(self.report(week='2026-10-25')['counts']['tasks'],0)

 def test_reopen_and_recomplete_move_latest_fact_and_do_not_double_count(self):
  t=self.raw(title='再次整理',completed_at=stamp('2026-10-20T12:00:00+08:00'))
  self.assertEqual(self.report(week='2026-10-20')['counts']['tasks'],1)
  self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'todo'})
  self.assertEqual(self.report(week='2026-10-20')['counts']['tasks'],0)
  t=next(record for record in self.snap(self.dad)['tasks'] if record['id']==t['id'])
  self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'})
  self.assertEqual(self.report()['counts']['tasks'],1);self.assertEqual(self.report(week='2026-10-20')['counts']['tasks'],0)

 def test_deleted_or_edited_completed_items_are_recalculated(self):
  i=self.raw('item');self.assertEqual(self.report()['counts']['items'],1)
  self.call(self.dad,'item.delete',{'id':i['id'],'version':i['version']});self.assertEqual(self.report()['counts']['items'],0)
  deleted=self.snap(self.dad)['deleted_items'][0];self.call(self.dad,'item.restore',{'id':i['id'],'version':deleted['version']});self.assertEqual(self.report()['counts']['items'],1)
  i=next(record for record in self.snap(self.dad)['items'] if record['id']==i['id'])
  self.call(self.dad,'item.update',{'id':i['id'],'version':i['version'],'quantity':3});self.assertEqual(self.report()['counts']['items'],0)

 def test_missing_invalid_and_future_completion_times_are_not_invented(self):
  for value in (None,True,'2026-10-27',float('nan'),1e99,stamp('2026-10-29T12:00:00+00:00')):self.raw(completed_at=value)
  self.raw();self.raw(status='pending');self.raw(status='cancelled')
  r=self.report();self.assertTrue(r['missing_completion_dates']);self.assertEqual(r['counts']['tasks'],1)

 def test_member_scope_and_current_membership_apply_to_current_and_history(self):
  with self.assertRaises(d.Problem):self.report(self.other)
  inv=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,inv['invite_code'])
  self.raw(title='我的记录',assignee=self.other);self.raw(title='别人的记录');r=self.report(self.other)
  self.assertEqual(r['scope'],'own');self.assertEqual(r['counts']['tasks'],1);self.assertNotIn('别人的记录',json.dumps(r,ensure_ascii=False))
  self.call(self.dad,'member.remove',{'user_id':self.other})
  with self.assertRaises(d.Problem):self.report(self.other,week='2026-10-20')

 def test_health_revocation_filters_all_report_sections_without_cached_totals(self):
  p=self.pregnancy();self.raw(title='私密已完成',pregnancy_id=p['id'],private_health_anchor=True)
  self.raw(title='私密安排',pregnancy_id=p['id'],private_health_anchor=True,status='todo',due_date='2026-10-28')
  self.call(self.mom,'appointment.create',{'title':'私密预约','date':'2026-10-28'})
  self.grant('health_read');r=self.report();self.assertEqual(r['counts']['tasks'],1);self.assertIn('私密预约',json.dumps(r,ensure_ascii=False))
  self.grant('health_read',False);r=self.report();self.assertEqual(r['counts']['tasks'],0);self.assertNotIn('私密',json.dumps(r,ensure_ascii=False))

 def test_baby_consent_is_independent_and_hidden_baby_status_does_not_change_report(self):
  p=self.pregnancy();self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':'2026-10-29'});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':'2026-10-28','babies':1,'guardian_consent':True,'birth_outcomes':['growing']})
  import lifecycle
  self.raw(title='宝宝私密记录',pregnancy_id=p['id'],template_key=next(iter(lifecycle.BABY_TEMPLATE_KEYS)))
  self.grant('health_read');before=self.report();self.assertNotIn('宝宝私密记录',json.dumps(before,ensure_ascii=False))
  with d.transaction() as c:
   b=d.rows(c,'baby',self.fid)[0];b['status']='hospitalized';d.put(c,'baby',b,self.fid)
  self.assertEqual(self.report(),before)
  self.grant('baby_read');r=self.report();self.assertIn('宝宝私密记录',json.dumps(r,ensure_ascii=False));self.assertFalse(r['briefing']['enabled']);self.assertEqual(r['briefing']['entries'],[])
  self.grant('baby_read',False);self.assertEqual(self.report(),before)

 def test_briefing_selects_week_dates_first_then_undated_then_priority_items(self):
  self.clear_open();self.raw(title='下周任务',status='todo',due_date='2026-11-03');self.raw(title='本周任务',status='todo',due_date='2026-10-29');self.raw(title='无日期任务',status='todo')
  self.raw('appointment',title='本周预约',owner=self.dad,date='2026-10-28',status='scheduled',pregnancy_id=None)
  self.raw('item',name='可选物品',level='可选',status='todo');required=self.raw('item',name='必备物品',level='必备',status='todo')
  r=self.report();self.assertEqual([e['title'] for e in r['briefing']['entries']],['本周预约','本周任务','无日期任务'])
  with d.transaction() as c:
   for t in d.rows(c,'task',self.fid):t['status']='done';d.put(c,'task',t,self.fid)
  r=self.report();self.assertEqual([e['title'] for e in r['briefing']['entries']],['本周预约','必备物品','可选物品'])
  self.assertEqual(r['briefing']['knowledge'][0]['topic'],'备产准备')

 def test_pause_archive_and_end_suppress_briefing_but_retain_facts(self):
  p=self.pregnancy();self.raw();self.grant('health_read')
  for changes in ({'paused':True},{'archived':True},{'state':'ended'}):
   with self.subTest(changes=changes):
    with d.transaction() as c:d.put(c,'pregnancy',dict(p,**changes),self.fid)
    r=self.report();self.assertEqual(r['counts']['tasks'],1);self.assertFalse(r['briefing']['enabled']);self.assertEqual(r['briefing']['knowledge'],[])

 def test_withdrawn_expired_or_clinical_references_never_enter_static_briefing(self):
  with d.transaction() as c:
   for k in d.rows(c,'knowledge'):k['status']='retired';d.put(c,'knowledge',k)
   for id,changes in (('valid',{}),('expired',{'valid_until':'2026-10-27'}),('clinical',{'clinical':True}),('draft',{'status':'draft'})):
    obj={'id':id,'topic':'家庭协作','title':id,'body':'一般生活内容','source':'合成来源','content_version':1,'status':'published','clinical':False};obj.update(changes);d.put(c,'knowledge',obj)
  r=self.report();self.assertEqual([k['id'] for k in r['briefing']['knowledge']],['valid'])
  with d.transaction() as c:
   k=d.get(c,'knowledge','valid');k['status']='retired';d.put(c,'knowledge',k)
  self.assertEqual(self.report()['briefing']['knowledge'],[])

 def test_report_read_never_mutates_records_or_creates_report_cache(self):
  self.raw()
  with d.transaction() as c:before=[tuple(row) for row in c.execute('SELECT * FROM records ORDER BY kind,id')]
  self.report();self.report(week='2026-10-20')
  with d.transaction() as c:self.assertEqual(before,[tuple(row) for row in c.execute('SELECT * FROM records ORDER BY kind,id')])

 def test_baby_status_from_archived_journey_does_not_suppress_new_journey_briefing(self):
  self.pregnancy()
  with d.transaction() as c:
   old={'id':d.uid(),'subject_id':self.mom,'state':'postpartum','birth_date':'2026-01-01','archived':True}
   d.put(c,'pregnancy',old,self.fid)
   d.put(c,'baby',{'id':d.uid(),'pregnancy_id':old['id'],'status':'lost'},self.fid)
  r=self.report(self.mom);self.assertTrue(r['briefing']['enabled']);self.assertTrue(r['briefing']['entries'])
