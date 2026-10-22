import datetime
import unittest
from unittest.mock import patch
import test_domain
import domain as d


class PregnancyEventTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def pregnancy(self,share=False):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  if share:self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_read','enabled':True})
  return self.snap(self.mom)['pregnancy']
 def set_date(self,p,offset=100):
  return self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=offset))})
 def records(self,kind):
  with d.transaction() as c:return d.rows(c,kind,self.fid)

 def test_command_replay_preserves_one_event_and_one_outbox(self):
  p=self.pregnancy();data={'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=100))}
  with d.transaction() as c:first=d.command(c,self.mom,self.fid,'pregnancy.event','event-retry',data)
  with d.transaction() as c:second=d.command(c,self.mom,self.fid,'pregnancy.event','event-retry',data)
  self.assertEqual(first,second)
  self.assertEqual(len(self.records('pregnancy_event')),1);self.assertEqual(len(self.records('outbox')),1)
  event=self.records('pregnancy_event')[0]
  self.assertEqual(event['before']['state'],'dating');self.assertEqual(event['after']['state'],'pregnant')
  self.assertEqual(event['id'],first['event_id'])

 def test_history_requires_current_subject_authorization(self):
  p=self.pregnancy();self.set_date(p)
  with self.assertRaises(d.Problem):
   with d.transaction() as c:d.pregnancy_history(c,self.dad,self.fid,p['id'])
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_read','enabled':True})
  with d.transaction() as c:self.assertEqual(len(d.pregnancy_history(c,self.dad,self.fid,p['id'])),1)
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_read','enabled':False})
  with self.assertRaises(d.Problem):
   with d.transaction() as c:d.pregnancy_history(c,self.dad,self.fid,p['id'])

 def test_outbox_rechecks_authorization_and_deduplicates(self):
  p=self.pregnancy(share=True);event=self.set_date(p)
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_read','enabled':False})
  d.tick();d.tick()
  notices=[n for n in self.records('notification') if n.get('ref')==event['event_id']]
  self.assertEqual([n['recipient'] for n in notices],[self.mom]);self.assertEqual(self.records('outbox')[0]['status'],'sent')

 def test_stale_revision_outbox_is_cancelled(self):
  p=self.pregnancy();old=self.set_date(p);p=self.snap(self.mom)['pregnancy'];latest=self.set_date(p,101)
  d.tick();events={e['event_id']:e for e in self.records('outbox')}
  self.assertEqual(events[old['event_id']]['status'],'cancelled');self.assertEqual(events[latest['event_id']]['status'],'sent')
  self.assertFalse(any(n.get('ref')==old['event_id'] for n in self.records('notification')))

 def test_sent_history_does_not_starve_pending_outbox(self):
  p=self.pregnancy();event=self.set_date(p)
  with d.transaction() as c:
   for i in range(101):d.put(c,'outbox',{'id':'old-'+str(i),'status':'sent','created_at':0,'pregnancy_id':p['id']},self.fid)
  d.tick();self.assertTrue(any(n.get('ref')==event['event_id'] for n in self.records('notification')))

 def test_worker_failure_can_retry_without_duplicate_notification(self):
  p=self.pregnancy();event=self.set_date(p)
  with patch.object(d,'require_active',side_effect=d.Problem(503,'合成处理故障')):
   with d.transaction() as c:d.process_outbox(c,self.fid)
  out=self.records('outbox')[0];self.assertEqual(out['attempts'],1);self.assertEqual(out['status'],'pending')
  with d.transaction() as c:out['available_at']=0;d.put(c,'outbox',out,self.fid)
  d.tick();d.tick()
  self.assertEqual(len([n for n in self.records('notification') if n.get('ref')==event['event_id']]),1)
  self.assertEqual(self.records('outbox')[0]['attempts'],2)

 def test_delete_subject_removes_private_event_history_and_queue(self):
  p=self.pregnancy();self.set_date(p)
  with d.transaction() as c:d.delete_account(c,self.mom,'long-password-123')
  self.assertEqual(self.records('pregnancy_event'),[]);self.assertEqual(self.records('outbox'),[])

 def test_replaying_delegated_event_rechecks_edit_grant(self):
  p=self.pregnancy(share=True)
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_edit','enabled':True})
  data={'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+datetime.timedelta(days=100))}
  with d.transaction() as c:d.command(c,self.dad,self.fid,'pregnancy.event','delegated-event',data)
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_edit','enabled':False})
  with self.assertRaises(d.Problem) as err:
   with d.transaction() as c:d.command(c,self.dad,self.fid,'pregnancy.event','delegated-event',data)
  self.assertEqual(err.exception.status,403)
