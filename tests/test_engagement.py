import datetime as dt
import unittest
from unittest.mock import patch

import test_domain
import domain as d


class EngagementTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def e(self,user=None):return self.snap(user or self.dad)['engagement']
 def badge(self,id,user=None):return next(b for b in self.e(user)['badges'] if b['id']==id)
 def task(self,type='家庭协作',user=None):return self.call(self.dad,'task.create',{'title':'合成分担','type':type,'assignee':user or self.dad})['id']
 def update(self,id,status,user=None):
  task=next(t for t in self.snap(user or self.dad)['tasks'] if t['id']==id);return self.call(user or self.dad,'task.update',{'id':id,'version':task['version'],'status':status})

 def test_catalog_has_explicit_rules_four_groups_and_no_scores_or_ranking(self):
  e=self.e();self.assertEqual(e['catalog_version'],1);self.assertEqual(len(e['badges']),15);self.assertEqual(len({b['group'] for b in e['badges']}),4)
  self.assertEqual(len({b['id'] for b in e['badges']}),15);self.assertTrue(all(b['rule'] and b['target']>0 for b in e['badges']))
  self.assertFalse(any(b['earned'] for b in e['badges']));self.assertNotIn('rank',e);self.assertNotIn('score',e)

 def test_completed_task_and_item_evidence_and_undo_are_real_and_private(self):
  id=self.task();self.update(id,'done');self.assertTrue(self.badge('first-action')['earned']);self.assertFalse(self.badge('first-action',self.mom)['earned'])
  self.update(id,'todo');self.assertFalse(self.badge('first-action')['earned']);self.update(id,'done');self.assertEqual(self.badge('five-actions')['progress'],1)
  item=self.snap(self.dad)['items'][0];self.call(self.dad,'item.update',{'id':item['id'],'version':item['version'],'status':'done'});self.assertTrue(self.badge('first-ready')['earned'])
  item=next(i for i in self.snap(self.dad)['items'] if i['id']==item['id']);self.call(self.dad,'item.update',{'id':item['id'],'version':item['version'],'quantity':2});self.assertFalse(self.badge('first-ready')['earned'])

 def test_pending_approval_is_not_an_achievement_and_approver_does_not_receive_credit(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  id=self.task();result=self.update(id,'done');self.assertFalse(self.badge('first-action')['earned'])
  a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==result['approval_id']);self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  self.assertTrue(self.badge('first-action')['earned']);self.assertFalse(self.badge('first-action',self.mom)['earned'])

 def test_reassignment_cannot_rewrite_who_completed_the_task(self):
  id=self.task();self.update(id,'done');task=next(t for t in self.snap(self.dad)['tasks'] if t['id']==id)
  self.call(self.dad,'task.update',{'id':id,'version':task['version'],'assignee':self.mom})
  self.assertTrue(self.badge('first-action')['earned']);self.assertFalse(self.badge('first-action',self.mom)['earned']);self.assertEqual(self.e(self.mom)['legacy_unattributed'],0)

 def test_legacy_done_records_are_not_attributed_from_current_assignment(self):
  with d.transaction() as c:
   t=d.rows(c,'task',self.fid)[0];t.update(status='done',completed_at=d.now());d.put(c,'task',t,self.fid)
  self.assertFalse(self.badge('first-action')['earned']);self.assertEqual(self.e()['legacy_unattributed'],1)

 def test_category_progress_uses_distinct_completed_resources(self):
  for _ in range(5):self.update(self.task(),'done')
  for _ in range(3):self.update(self.task('陪伴'),'done')
  self.assertTrue(self.badge('housework')['earned']);self.assertTrue(self.badge('listening')['earned']);self.assertTrue(self.badge('five-actions')['earned'])

 def test_daily_checkin_is_idempotent_and_never_backfills(self):
  r=self.call(self.dad,'engagement.checkin',{'confirmed':True});self.call(self.dad,'engagement.checkin',{'confirmed':True})
  self.assertEqual(self.e()['checkin_days'],1);self.assertTrue(self.e()['checked_today']);self.assertEqual(r['date'],str(d.today()))
  for data in ({'confirmed':False},{'confirmed':True,'date':str(d.today()-dt.timedelta(days=1))}):
   with self.assertRaises(d.Problem):self.call(self.dad,'engagement.checkin',data)
  with self.assertRaises(d.Problem):self.call(self.other,'engagement.checkin',{'confirmed':True})

 def test_streaks_handle_cross_year_and_natural_break_without_erasing_longest(self):
  for offset in range(30):
   with patch.object(d,'today',return_value=dt.date(2025,12,20)+dt.timedelta(days=offset)):self.call(self.dad,'engagement.checkin',{'confirmed':True})
  with patch.object(d,'today',return_value=dt.date(2026,1,19)):
   self.assertEqual(self.e()['current_streak'],30);self.assertTrue(self.badge('checkin-thirty')['earned'])
  with patch.object(d,'today',return_value=dt.date(2026,1,22)):
   self.assertEqual(self.e()['current_streak'],0);self.assertEqual(self.e()['longest_streak'],30);self.assertTrue(self.badge('checkin-thirty')['earned'])

 def test_support_requires_current_visibility_completed_date_and_personal_confirmation(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});a_id=self.call(self.mom,'appointment.create',{'title':'已确认预约','date':str(d.today())})['id']
  a=self.snap(self.mom)['appointments'][0]
  with self.assertRaises(d.Problem):self.call(self.mom,'engagement.support',{'id':a_id,'version':a['version'],'confirmed':True})
  self.call(self.mom,'appointment.update',{'id':a_id,'version':a['version'],'status':'done'});a=self.snap(self.mom)['appointments'][0]
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  self.call(self.dad,'engagement.support',{'id':a_id,'version':a['version'],'confirmed':True});self.call(self.dad,'engagement.support',{'id':a_id,'version':a['version'],'confirmed':True})
  self.assertTrue(self.badge('support')['earned']);self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':False});self.assertFalse(self.badge('support')['earned'])

 def test_quiet_and_birth_commemoration_use_only_visible_baby_state(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+dt.timedelta(days=1))});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':1,'guardian_consent':True,'birth_outcomes':['growing']})
  self.assertFalse(self.badge('new-journey')['earned']);before=self.e()
  with d.transaction() as c:b=d.rows(c,'baby',self.fid)[0];b['status']='hospitalized';d.put(c,'baby',b,self.fid)
  self.assertEqual(self.e(),before)
  self.call(self.mom,'consent.update',{'scope':'baby_read','grantee':self.dad,'enabled':True,'guardian_consent':True});self.assertTrue(self.e()['quiet']);self.assertFalse(self.badge('new-journey')['earned'])
  with d.transaction() as c:b=d.rows(c,'baby',self.fid)[0];b['status']='growing';d.put(c,'baby',b,self.fid)
  self.assertTrue(self.badge('new-journey')['earned']);self.call(self.dad,'engagement.preferences',{'celebrations':False});self.assertTrue(self.e()['quiet'])

 def test_health_deletion_removes_evidence_and_personal_checkins(self):
  id=self.task(user=self.mom);self.update(id,'done',self.mom);self.call(self.mom,'engagement.checkin',{'confirmed':True})
  with d.transaction() as c:d.delete_account(c,self.mom,'long-password-123');self.assertFalse(any(r['user_id']==self.mom for r in d.rows(c,'completion_fact')));self.assertEqual(d.rows(c,'checkin'),[])

 def test_legacy_growing_baby_without_guardian_confirmation_does_not_auto_award_commemoration(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
  with d.transaction() as c:d.put(c,'baby',{'id':'legacy-baby','pregnancy_id':p['id'],'status':'growing','birth_date':str(d.today())},self.fid)
  self.assertFalse(self.badge('new-journey',self.mom)['earned'])
