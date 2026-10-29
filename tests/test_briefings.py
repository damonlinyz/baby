import datetime as dt
import unittest

import test_domain
from test_reminder_preferences import clock
import domain as d
import briefings


class BriefingTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def rows(self,user=None):
  with d.transaction() as c:return [r for r in d.rows(c,'briefing',self.fid) if r['recipient']==(user or self.dad)]
 def read(self,id,user=None):
  with d.transaction() as c:return briefings.read(c,self.fid,user or self.dad,id)
 def seed(self):
  with d.transaction() as c:d.seed_knowledge(c)

 def test_monday_eight_local_time_generates_once_for_each_recipient(self):
  self.seed()
  with clock('2026-11-02T07:59:00+08:00'):d.tick();self.assertEqual(self.rows(),[])
  with clock('2026-11-02T08:00:00+08:00'):d.tick();before=self.rows();d.tick();self.assertEqual(self.rows(),before)
  self.assertEqual(len(before),1);self.assertEqual(len(self.rows(self.mom)),1);self.assertEqual(before[0]['week_start'],'2026-11-02')
  self.assertEqual(before[0]['recipient'],self.dad);self.assertNotEqual(before[0]['id'],self.rows(self.mom)[0]['id'])

 def test_snapshots_freeze_facts_and_past_weeks_do_not_backfill(self):
  with clock('2026-11-02T08:00:00+08:00'):
   d.tick();first=self.rows()[0];t=self.snap(self.dad)['tasks'][0];self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'status':'done'});d.tick()
   self.assertEqual(self.read(first['id'])['payload']['counts']['tasks'],0);self.assertEqual(self.snap(self.dad)['weekly_report']['counts']['tasks'],1)
  with clock('2026-11-16T09:00:00+08:00'):
   d.tick();self.assertEqual({r['week_start'] for r in self.rows()},{'2026-11-02','2026-11-16'});self.assertEqual(self.read(first['id'])['status'],'active')

 def test_only_original_recipient_with_current_membership_reads_a_snapshot(self):
  with clock('2026-11-02T08:00:00+08:00'):
   d.tick();id=self.rows()[0]['id']
   for user in (self.mom,self.other):
    with self.assertRaises(d.Problem):self.read(id,user)
   self.call(self.mom,'member.leave',{});self.assertEqual(self.read(id)['status'],'invalid');self.assertIsNone(self.rows()[0]['payload'])

 def test_revoke_and_regrant_never_revive_old_payload_and_next_tick_rebuilds_current(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  with clock('2026-11-02T08:00:00+08:00'):
   d.tick();first=self.rows()[0];self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':False});self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
   self.assertEqual(self.read(first['id'])['status'],'invalid');self.assertIsNone(self.rows()[0]['payload']);d.tick();self.assertEqual(self.rows()[0]['revision'],first['revision']+1)

 def test_dates_and_published_reference_changes_invalidate_frozen_content(self):
  self.seed()
  with clock('2026-11-02T08:00:00+08:00'):
   d.tick();row=self.rows()[0];ref=row['citations'][0]['id']
   with d.transaction() as c:k=d.get(c,'knowledge',ref);k['status']='retired';d.put(c,'knowledge',k)
   self.assertEqual(self.read(row['id'])['status'],'invalid');d.tick();self.assertEqual(self.rows()[0]['revision'],2)
   self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy'];mom=self.rows(self.mom)[0]
   self.assertEqual(self.read(mom['id'],self.mom)['status'],'invalid')

 def test_pause_keeps_manual_facts_but_stops_auto_snapshot_and_pending_notice(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});self.call(self.mom,'notification.preferences',{'weekly_enabled':True,'quiet_enabled':True,'quiet_start':'00:00','quiet_end':'23:59'})
  with clock('2026-11-02T09:00:00+08:00'):
   d.tick();row=self.rows(self.mom)[0];p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'pause'});d.tick()
   self.assertEqual(self.read(row['id'],self.mom)['status'],'invalid');self.assertEqual(self.call(self.mom,'briefing.refresh',{})['id'],None)
   self.assertEqual(self.snap(self.mom)['weekly_report']['counts'],{'tasks':0,'items':0})

 def test_weekly_notification_is_opt_in_and_subject_to_quiet_hours(self):
  self.call(self.dad,'notification.preferences',{'weekly_enabled':True,'quiet_enabled':True,'quiet_start':'08:00','quiet_end':'10:00'})
  with clock('2026-11-02T09:00:00+08:00'):d.tick();self.assertFalse(any(n.get('reminder_type')=='weekly' for n in self.snap(self.dad)['notifications']))
  with clock('2026-11-02T10:00:00+08:00'):
   d.tick();d.tick();self.assertEqual(sum(n.get('reminder_type')=='weekly' for n in self.snap(self.dad)['notifications']),1)
   self.assertFalse(any(n.get('reminder_type')=='weekly' for n in self.snap(self.mom)['notifications']))

 def test_visible_baby_context_is_stable_and_hidden_baby_status_never_invalidates_other_receivers(self):
  self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
  with d.transaction() as c:d.put(c,'baby',{'id':'synthetic-baby','pregnancy_id':p['id'],'status':'growing','birth_date':str(d.today())},self.fid)
  with clock('2026-11-02T09:00:00+08:00'):
   d.tick();dad=self.rows()[0];mom=self.rows(self.mom)[0];d.tick();self.assertEqual(self.rows(self.mom)[0]['revision'],mom['revision'])
   with d.transaction() as c:b=d.get(c,'baby','synthetic-baby');b['status']='hospitalized';d.put(c,'baby',b,self.fid)
   self.assertEqual(self.read(dad['id'])['status'],'active');self.assertEqual(self.read(mom['id'],self.mom)['status'],'invalid')

 def test_restore_preserves_owners_valid_history_but_never_restores_shared_authorization(self):
  import maintenance
  from unittest.mock import patch
  self.call(self.mom,'pregnancy.create',{'consent':True});self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  with clock('2026-11-02T09:00:00+08:00'):
   d.tick();dad=self.rows()[0];mom=self.rows(self.mom)[0];snapshot=d.DB.parent/'weekly.sqlite3';maintenance.backup(d.DB,snapshot)
   restored=d.DB.parent/'weekly-restored.sqlite3';maintenance.restore(snapshot,d.deletion_ledger_path(),restored)
   with patch.object(d,'DB',restored):
    self.assertEqual(self.read(mom['id'],self.mom)['status'],'active');self.assertEqual(self.read(dad['id'])['status'],'invalid')
