import datetime
import threading
import unittest
from unittest.mock import patch

import test_domain
import domain as d
import maintenance as m
import recurring


class RecurringTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def create(self,**changes):
  data={'title':'整理厨房','note':'按实际精力安排','repeat':'daily','repeat_every':1,'due_date':str(d.today())}
  data.update(changes)
  return self.call(self.dad,'task.create',data)
 def instances(self,sid):return [t for t in self.snap(self.dad)['tasks'] if t.get('series_id')==sid]
 def series(self,sid):return next(s for s in self.snap(self.dad)['task_series'] if s['id']==sid)
 def update(self,task,user=None,**changes):return self.call(user or self.dad,'task.update',dict(changes,id=task['id'],version=task['version']))
 def stop(self,sid,user=None):
  s=self.series(sid);return self.call(user or self.dad,'task_series.stop',{'id':sid,'version':s['version']})

 def test_daily_instances_are_independent_and_tick_does_not_duplicate_them(self):
  sid=self.create()['series_id'];tasks=self.instances(sid)
  self.assertEqual(len(tasks),15);self.assertEqual(len({t['id'] for t in tasks}),15)
  self.assertEqual({t['due_date'] for t in tasks},{str(d.today()+datetime.timedelta(days=i)) for i in range(15)})
  self.update(tasks[0],status='done');self.update(tasks[1],status='skipped');d.tick();d.tick()
  tasks=self.instances(sid);self.assertEqual(len(tasks),15);self.assertEqual(tasks[0]['status'],'done');self.assertEqual(tasks[1]['status'],'skipped')

 def test_weekly_interval_and_inclusive_end_date(self):
  sid=self.create(repeat='weekly',repeat_every=2,repeat_until=str(d.today()+datetime.timedelta(days=14)))['series_id']
  self.assertEqual([t['due_date'] for t in self.instances(sid)],[str(d.today()),str(d.today()+datetime.timedelta(days=14))])
  with patch.object(d,'today',return_value=d.today()+datetime.timedelta(days=15)):
   d.tick();self.assertEqual(self.series(sid)['status'],'finished');self.assertEqual(len(self.instances(sid)),2)

 def test_future_start_has_no_premature_occurrences(self):
  current=d.today();sid=self.create(due_date=str(current+datetime.timedelta(days=30)))['series_id']
  self.assertEqual(self.instances(sid),[])
  with patch.object(d,'today',return_value=current+datetime.timedelta(days=16)):
   d.tick();self.assertEqual([t['due_date'] for t in self.instances(sid)],[str(current+datetime.timedelta(days=30))])

 def test_resume_after_outage_does_not_backfill_unplanned_past_dates(self):
  current=d.today();sid=self.create()['series_id'];original={t['series_date'] for t in self.instances(sid)}
  with patch.object(d,'today',return_value=current+datetime.timedelta(days=60)):
   d.tick();tasks=self.instances(sid)
  added={t['series_date'] for t in tasks}-original
  self.assertEqual(len(added),15);self.assertEqual(min(added),str(current+datetime.timedelta(days=60)))

 def test_single_occurrence_edit_is_not_overwritten_or_regenerated(self):
  sid=self.create()['series_id'];task=self.instances(sid)[0]
  self.update(task,due_date=str(d.today()+datetime.timedelta(days=90)),title='只改这一次')
  d.tick();tasks=self.instances(sid);self.assertEqual(len(tasks),15)
  changed=next(t for t in tasks if t['id']==task['id'])
  self.assertEqual(changed['title'],'只改这一次');self.assertEqual(changed['due_date'],str(d.today()+datetime.timedelta(days=90)))
  self.assertEqual(changed['series_date'],str(d.today()));self.assertTrue(all(t['title']=='整理厨房' for t in tasks if t['id']!=task['id']))

 def test_stop_preserves_completed_history_and_cancels_pending_approvals_and_notices(self):
  sid=self.create()['series_id'];tasks=self.instances(sid)
  self.update(tasks[0],status='done');self.update(tasks[1],status='skipped');self.update(tasks[3],status='cancelled')
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  pending=self.update(tasks[2],status='done');d.tick()
  result=self.stop(sid);self.assertEqual(result['cancelled_occurrences'],12)
  tasks=self.instances(sid);self.assertEqual(tasks[0]['status'],'done');self.assertEqual(tasks[1]['status'],'skipped')
  self.assertTrue(all(t['status']=='cancelled' for t in tasks[2:]))
  a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==pending['approval_id']);self.assertEqual(a['status'],'cancelled')
  with self.assertRaises(d.Problem):self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  with d.transaction() as c:
   refs={t['id'] for t in tasks[2:]}|{pending['approval_id']}
   self.assertTrue(all(n.get('cancelled') or n['read'] for n in d.rows(c,'notification',self.fid) if n.get('ref') in refs))
  d.tick();self.assertEqual(len(self.instances(sid)),15)
  with self.assertRaises(d.Problem):self.update(tasks[2],status='todo')
  with self.assertRaises(d.Problem):self.update(tasks[3],status='todo')
  with self.assertRaises(d.Problem):self.update(tasks[2],title='不能修改已停止实例')

 def test_only_coordinator_creates_or_stops_series_and_executor_only_completes_own(self):
  sid=self.create(assignee=self.mom)['series_id'];task=self.instances(sid)[0]
  for user in (self.mom,self.other):
   with self.assertRaises(d.Problem):self.call(user,'task.create',{'title':'伪造','repeat':'daily','due_date':str(d.today())})
   with self.assertRaises(d.Problem):self.stop(sid,user)
  with self.assertRaises(d.Problem):self.update(task,status='done')
  self.update(task,self.mom,status='done');self.assertEqual(self.instances(sid)[0]['status'],'done')

 def test_basic_members_only_see_their_own_series_and_instances(self):
  invite=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,invite['invite_code'])
  own=self.create(assignee=self.other)['series_id'];self.create()
  s=self.snap(self.other);self.assertEqual([x['id'] for x in s['task_series']],[own]);self.assertTrue(all(t['series_id']==own for t in s['tasks']))

 def test_assignee_departure_stops_generation_and_cancels_unfinished_instances(self):
  sid=self.create(assignee=self.mom)['series_id'];self.call(self.mom,'member.leave',{})
  self.assertEqual(self.series(sid)['stopped_reason'],'assignee_left');self.assertTrue(all(t['status']=='cancelled' for t in self.instances(sid)))
  with patch.object(d,'today',return_value=d.today()+datetime.timedelta(days=30)):d.tick()
  self.assertEqual(len(self.instances(sid)),15)

 def test_account_deletion_and_restore_replay_do_not_restart_assignee_series(self):
  sid=self.create(assignee=self.mom)['series_id'];snapshot=d.DB.parent/'old.sqlite3';m.backup(d.DB,snapshot)
  with d.transaction() as c:d.delete_account(c,self.mom,'long-password-123')
  self.assertEqual(self.series(sid)['status'],'stopped')
  restored=d.DB.parent/'restored.sqlite3';m.restore(snapshot,d.deletion_ledger_path(),restored)
  with patch.object(d,'DB',restored):
   d.tick();self.assertEqual(self.series(sid)['status'],'stopped');self.assertTrue(all(t['status']=='cancelled' for t in self.instances(sid)))

 def test_household_series_are_not_cancelled_by_pregnancy_events(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});sid=self.create()['series_id']
  p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'end','date':str(d.today())})
  d.tick();self.assertEqual(self.series(sid)['status'],'active');self.assertTrue(all(t['status']=='todo' and t['pregnancy_id'] is None for t in self.instances(sid)))

 def test_family_timezone_drives_calendar_dates_without_24_hour_arithmetic(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'timezone':'America/New_York'})
  with patch.object(d,'today',return_value=datetime.date(2026,3,7)):
   sid=self.create()['series_id'];self.assertEqual(self.snap(self.dad)['calendar_date'],'2026-03-07')
   self.assertEqual([t['due_date'] for t in self.instances(sid)][:3],['2026-03-07','2026-03-08','2026-03-09'])

 def test_invalid_recurrence_values_are_rejected_atomically(self):
  values=[{'repeat':'monthly'},{'repeat_every':0},{'repeat_every':31},{'repeat_every':True},{'repeat_every':'2'},
          {'due_date':None},{'due_date':str(d.today()-datetime.timedelta(days=1))},
          {'repeat_until':str(d.today()-datetime.timedelta(days=1))},{'assignee':self.other},{'note':'x'*501}]
  for changes in values:
   with self.subTest(changes=changes):
    with self.assertRaises(d.Problem):self.create(**changes)
  self.assertEqual(self.snap(self.dad)['task_series'],[])

 def test_stop_rechecks_version_and_family_and_is_idempotent(self):
  sid=self.create()['series_id'];s=self.series(sid);self.stop(sid)
  with self.assertRaises(d.Problem):self.call(self.dad,'task_series.stop',{'id':sid,'version':s['version']})
  self.assertEqual(self.stop(sid)['cancelled_occurrences'],0)
  latest=self.series(sid)['version']
  with d.transaction() as c:
   other=d.create_family(c,self.dad,{'name':'另一个家庭'})['family_id']
   with self.assertRaises(d.Problem):d.mutate(c,self.dad,other,'task_series.stop',{'id':sid,'version':latest})

 def test_command_replay_and_concurrent_ticks_never_duplicate_series_occurrences(self):
  data={'title':'幂等家务','repeat':'daily','due_date':str(d.today())}
  with d.transaction() as c:first=d.command(c,self.dad,self.fid,'task.create','repeat-key',data)
  with d.transaction() as c:self.assertEqual(d.command(c,self.dad,self.fid,'task.create','repeat-key',data),first)
  errors=[]
  def run():
   try:d.tick()
   except Exception as e:errors.append(e)
  workers=[threading.Thread(target=run) for _ in range(2)]
  for worker in workers:worker.start()
  for worker in workers:worker.join()
  self.assertEqual(errors,[]);self.assertEqual(len(self.instances(first['series_id'])),15);self.assertEqual(len(self.snap(self.dad)['task_series']),1)
