import datetime as dt
import threading
import unittest
from unittest.mock import patch

import test_domain
import test_recurring
import domain as d


class RecurringRuleTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 create=test_recurring.RecurringTests.create
 instances=test_recurring.RecurringTests.instances
 series=test_recurring.RecurringTests.series
 update=test_recurring.RecurringTests.update
 stop=test_recurring.RecurringTests.stop
 def change(self,sid,action='update',user=None,**changes):
  s=self.series(sid);return self.call(user or self.dad,'task_series.'+action,dict(changes,id=sid,version=s['version']))
 def at(self,days):return str(d.today()+dt.timedelta(days=days))
 def by_id(self,sid,id):return next(t for t in self.instances(sid) if t['id']==id)

 def test_rule_edit_rebuilds_future_todos_preserves_done_skipped_and_stable_ids(self):
  sid=self.create()['series_id'];tasks=self.instances(sid)
  self.update(tasks[0],status='done');self.update(tasks[1],status='skipped')
  done,skipped=self.instances(sid)[:2]
  result=self.change(sid,title='每两天收拾',repeat_every=2,assignee=self.mom)
  tasks=self.instances(sid);self.assertEqual(tasks[:2],[done,skipped]);self.assertEqual(len(tasks),15)
  for task in tasks[2:]:
   if (d.date(task['series_date'])-d.today()).days%2:
    self.assertEqual(task['series_locked'],'rule_changed')
   else:self.assertEqual((task['status'],task['title'],task['assignee']),('todo','每两天收拾',self.mom))
  self.assertEqual(result['updated_occurrences'],7);self.assertEqual(result['cancelled_occurrences'],6)
  self.assertEqual(self.series(sid)['rule_revision'],2);d.tick();self.assertEqual(len(self.instances(sid)),15)

 def test_changed_back_rule_reuses_cancelled_slots_but_never_reopens_manual_cancellation(self):
  sid=self.create()['series_id'];tasks=self.instances(sid);self.update(tasks[3],status='cancelled')
  self.change(sid,repeat='weekly');self.change(sid,repeat='daily')
  current=self.instances(sid);self.assertEqual(len(current),15);self.assertEqual({t['id'] for t in current},{t['id'] for t in tasks})
  self.assertEqual(current[3]['status'],'cancelled');self.assertFalse(current[3].get('series_locked'))
  self.assertTrue(all(t['status']=='todo' for t in current if t['id']!=tasks[3]['id']))

 def test_progress_pending_and_explicit_single_edits_are_preserved(self):
  sid=self.create()['series_id'];tasks=self.instances(sid)
  self.update(tasks[0],status='in_progress');self.update(tasks[1],note='本次备注')
  self.update(tasks[2],due_date=self.at(60));self.update(tasks[3],assignee=self.mom)
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  pending=self.update(tasks[4],status='done');before=self.instances(sid)[:5]
  self.change(sid,title='新规则',repeat='weekly',assignee=self.mom)
  self.assertEqual(self.instances(sid)[:5],before)
  a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==pending['approval_id'])
  self.assertEqual(a['status'],'pending');self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  self.assertEqual(self.by_id(sid,tasks[4]['id'])['status'],'done')

 def test_legacy_content_override_and_past_instances_are_not_overwritten(self):
  sid=self.create()['series_id'];tasks=self.instances(sid)
  with d.transaction() as c:
   task=d.get(c,'task',tasks[5]['id']);task['title']='旧客户端单次改名';d.put(c,'task',task,self.fid)
  old=self.instances(sid)
  with patch.object(d,'today',return_value=d.today()+dt.timedelta(days=3)):
   self.change(sid,title='后续新名称',repeat_every=2)
  current=self.instances(sid);self.assertEqual(current[:3],old[:3]);self.assertEqual(current[5],old[5])

 def test_pause_cancels_approvals_notices_and_blocks_single_edits(self):
  sid=self.create()['series_id'];tasks=self.instances(sid);self.update(tasks[0],status='done');self.update(tasks[1],status='skipped')
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  r=self.update(tasks[2],status='done');d.tick();before=self.instances(sid)[:2]
  result=self.change(sid,'pause');self.assertEqual(result['paused_occurrences'],13);self.assertEqual(self.instances(sid)[:2],before)
  paused=self.instances(sid)[2:];self.assertTrue(all(t['status']=='cancelled' and t['series_locked']=='paused' for t in paused))
  a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==r['approval_id']);self.assertEqual(a['status'],'cancelled')
  with self.assertRaises(d.Problem):self.update(paused[0],status='done')
  with self.assertRaises(d.Problem):self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  refs={t['id'] for t in paused}|{a['id'],sid}
  with d.transaction() as c:self.assertTrue(all(n.get('read') or n.get('cancelled') for n in d.rows(c,'notification',self.fid) if n.get('ref') in refs))
  with patch.object(d,'today',return_value=d.today()+dt.timedelta(days=30)):d.tick()
  self.assertEqual(len(self.instances(sid)),15)

 def test_resume_restores_future_ids_without_reviving_confirmations_or_past_instances(self):
  sid=self.create()['series_id'];tasks=self.instances(sid);self.update(tasks[1],status='in_progress');self.change(sid,'pause')
  with patch.object(d,'today',return_value=d.today()+dt.timedelta(days=2)):
   result=self.change(sid,'resume');self.assertEqual(result['resumed_occurrences'],13)
   current=self.instances(sid);self.assertEqual(len(current),17)
   self.assertEqual(current[0]['series_locked'],'pause_elapsed');self.assertEqual(current[1]['series_locked'],'pause_elapsed')
   self.assertTrue(all(t['status']=='todo' for t in current[2:]));self.assertEqual(result['created_occurrences'],2)
   d.tick();self.assertEqual(len(self.instances(sid)),17)

 def test_pending_resumes_as_in_progress_and_requires_fresh_approval(self):
  sid=self.create()['series_id'];task=self.instances(sid)[0]
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  original=self.update(task,status='done');self.change(sid,'pause');self.change(sid,'resume')
  current=self.instances(sid)[0];self.assertEqual(current['status'],'in_progress')
  new=self.update(current,status='done');self.assertNotEqual(new['approval_id'],original['approval_id'])

 def test_edit_paused_rule_keeps_pause_and_preserves_started_instances(self):
  sid=self.create()['series_id'];tasks=self.instances(sid);self.update(tasks[0],status='in_progress');self.change(sid,'pause')
  old=self.instances(sid)[0];self.change(sid,repeat='weekly',title='每周家务',assignee=self.mom)
  self.assertEqual(self.series(sid)['status'],'paused');self.assertEqual(self.instances(sid)[0],old)
  self.assertTrue(all(t['status']=='cancelled' for t in self.instances(sid)))
  self.change(sid,'resume');tasks=self.instances(sid)
  self.assertEqual(tasks[0]['status'],'in_progress');self.assertEqual(tasks[0]['assignee'],self.dad)
  self.assertEqual([t['title'] for t in tasks if t['status']=='todo'],['每周家务','每周家务'])

 def test_future_single_override_restores_even_if_original_slot_is_past(self):
  sid=self.create()['series_id'];task=self.instances(sid)[0];future=self.at(60);self.update(task,due_date=future);self.change(sid,'pause')
  with patch.object(d,'today',return_value=d.today()+dt.timedelta(days=30)):self.change(sid,'resume')
  restored=self.by_id(sid,task['id']);self.assertEqual(restored['status'],'todo');self.assertEqual(restored['due_date'],future)

 def test_expired_paused_rule_requires_extension_before_resume(self):
  sid=self.create(repeat_until=self.at(1))['series_id'];self.change(sid,'pause')
  with patch.object(d,'today',return_value=d.today()+dt.timedelta(days=2)):
   with self.assertRaises(d.Problem):self.change(sid,'resume')
   self.assertEqual(self.series(sid)['status'],'paused');self.change(sid,repeat_until=None);self.change(sid,'resume')
  self.assertEqual(self.series(sid)['status'],'active')

 def test_finished_rule_can_extend_but_stopped_rule_can_never_restart(self):
  sid=self.create(repeat_until=self.at(0))['series_id']
  with patch.object(d,'today',return_value=d.today()+dt.timedelta(days=1)):
   d.tick();self.assertEqual(self.series(sid)['status'],'finished');self.change(sid,repeat_until=None)
  self.assertEqual(self.series(sid)['status'],'active');self.change(sid,'pause');self.stop(sid)
  for action in ('update','pause','resume'):
   with self.assertRaises(d.Problem):self.change(sid,action,title='重启')
  self.assertTrue(all(t.get('series_stopped') for t in self.instances(sid)))

 def test_invalid_edits_are_atomic_and_past_anchor_can_be_retained(self):
  sid=self.create()['series_id'];before=self.instances(sid);series=self.series(sid)
  for changes in ({'repeat':'monthly'},{'repeat_every':True},{'repeat_every':'2'},{'repeat_every':0},{'repeat_every':31},{'title':' '},{'note':None},{'note':'x'*501},{'assignee':self.other},{'due_date':self.at(-1)},{'repeat_until':self.at(-1)}):
   with self.assertRaises(d.Problem):self.change(sid,**changes)
  self.assertEqual(self.instances(sid),before);self.assertEqual(self.series(sid),series)
  with patch.object(d,'today',return_value=d.today()+dt.timedelta(days=3)):self.change(sid,repeat_every=3,due_date=series['start_date'])
  self.assertEqual(self.series(sid)['start_date'],series['start_date'])

 def test_permissions_versions_and_family_scope_on_all_rule_actions(self):
  sid=self.create()['series_id'];series=self.series(sid)
  with d.transaction() as c:other_fid=d.create_family(c,self.dad,{'name':'其他家庭'})['family_id']
  for action in ('update','pause','resume'):
   with self.assertRaises(d.Problem):self.change(sid,action,user=self.mom)
   with self.assertRaises(d.Problem):self.call(self.dad,'task_series.'+action,{'id':sid,'version':series['version']+100})
   with self.assertRaises(d.Problem):
    with d.transaction() as c:d.mutate(c,self.dad,other_fid,'task_series.'+action,{'id':sid,'version':series['version']})

 def test_repeated_actions_and_lost_response_replay_do_not_undo_later_changes(self):
  sid=self.create()['series_id'];before=self.series(sid);self.change(sid,title=before['title']);self.assertEqual(self.series(sid),before)
  data={'id':sid,'version':before['version']}
  with d.transaction() as c:d.command(c,self.dad,self.fid,'task_series.pause','pause-replay',data)
  paused=self.series(sid);self.assertEqual(self.change(sid,'pause')['paused_occurrences'],0);self.assertEqual(self.series(sid),paused)
  self.change(sid,'resume');active=self.series(sid)
  with d.transaction() as c:d.command(c,self.dad,self.fid,'task_series.pause','pause-replay',data)
  self.assertEqual(self.series(sid),active);self.assertEqual(self.change(sid,'resume')['created_occurrences'],0)

 def test_departed_previous_executor_closes_preserved_instances_without_stopping_new_owner(self):
  sid=self.create(assignee=self.mom)['series_id'];task=self.instances(sid)[0];self.update(task,self.mom,status='in_progress')
  self.change(sid,assignee=self.dad);self.change(sid,'pause');self.call(self.mom,'member.leave',{})
  self.assertEqual(self.series(sid)['status'],'paused');self.assertEqual(self.by_id(sid,task['id'])['series_locked'],'assignee_inactive')
  self.change(sid,'resume');self.assertEqual(self.series(sid)['status'],'active');self.assertEqual(self.by_id(sid,task['id'])['status'],'cancelled')

 def test_current_executor_departure_stops_paused_rule(self):
  sid=self.create(assignee=self.mom)['series_id'];self.change(sid,'pause');self.call(self.mom,'member.leave',{})
  self.assertEqual(self.series(sid)['status'],'stopped')
  with self.assertRaises(d.Problem):self.change(sid,'resume')

 def test_concurrent_ticks_and_update_keep_one_occurrence_per_original_date(self):
  sid=self.create()['series_id'];errors=[]
  def run(fn):
   try:fn()
   except Exception as error:errors.append(error)
  workers=[threading.Thread(target=run,args=(fn,)) for fn in (d.tick,lambda:self.change(sid,repeat_every=2),d.tick)]
  for t in workers:t.start()
  for t in workers:t.join()
  self.assertEqual(errors,[]);tasks=self.instances(sid);self.assertEqual(len(tasks),15);self.assertEqual(len({t['series_date'] for t in tasks}),15)
  self.assertEqual(sum(t['status']=='todo' for t in tasks),8)

 def test_new_anchor_changes_calendar_phase_and_future_anchor_waits_for_window(self):
  sid=self.create()['series_id'];self.change(sid,repeat='weekly',due_date=self.at(3),repeat_until=self.at(14))
  self.assertEqual([t['due_date'] for t in self.instances(sid) if t['status']=='todo'],[self.at(3),self.at(10)])
  future=self.at(30);self.change(sid,due_date=future,repeat_until=None)
  self.assertTrue(all(t['status']=='cancelled' for t in self.instances(sid)))
  with patch.object(d,'today',return_value=d.today()+dt.timedelta(days=16)):d.tick()
  self.assertEqual([t['due_date'] for t in self.instances(sid) if t['status']=='todo'],[future])
