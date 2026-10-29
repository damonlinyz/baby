import json
import os
import threading
import unittest
from unittest.mock import patch

import test_domain
import domain as d
import push_notifications as push

REAL_SEND=push.send


class PushNotificationTests(unittest.TestCase):
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def setUp(self):
  self.env=patch.dict(os.environ,{'WX_NOTIFY_ENABLED':'1','WX_APP_ID':'synthetic-app','WX_APP_SECRET':'synthetic-secret','WX_REMINDER_TEMPLATE_ID':'synthetic-template'})
  self.env.start();self.addCleanup(self.env.stop)
  self.token_mock=patch.object(push,'get_token',return_value='synthetic-token');self.token=self.token_mock.start();self.addCleanup(self.token_mock.stop)
  self.send_mock=patch.object(push,'send',return_value={'errcode':0});self.send=self.send_mock.start();self.addCleanup(self.send_mock.stop)
  test_domain.DomainTests.setUp(self)
 def tearDown(self):test_domain.DomainTests.tearDown(self)
 def identity(self,user=None):
  user=user or self.dad
  with d.transaction() as c:d.put(c,'wechat_identity',{'id':user+'-wx','user_id':user,'appid':'synthetic-app','openid':'synthetic-openid-'+user})
 def subscribe(self,user=None):return self.call(user or self.dad,'notification.subscribe',{'accepted':True,'template_id':'synthetic-template','identity_id':(user or self.dad)+'-wx'})
 def prepare(self,user=None,count=1):
  user=user or self.dad;self.identity(user);self.call(user,'notification.preferences',{'external_enabled':True});self.subscribe(user)
  for _ in range(count):self.call(self.dad,'task.create',{'title':'不出站的私密标题','due_date':str(d.today()),'assignee':user})
  with patch.object(push,'dispatch'):d.tick()
 def deliveries(self):
  with d.transaction() as c:return d.rows(c,'external_delivery',self.fid)

 def test_disabled_configuration_and_missing_verified_identity_never_grant_or_send(self):
  with patch.dict(os.environ,{'WX_NOTIFY_ENABLED':'0'}):
   with self.assertRaises(d.Problem):self.subscribe()
   push.dispatch();self.send.assert_not_called();self.assertFalse(push.options()['configured'])
  with self.assertRaises(d.Problem):self.subscribe()
  self.identity()
  for data in ({'accepted':False,'template_id':'synthetic-template'},{'accepted':True,'template_id':'other-template'}):
   with self.assertRaises(d.Problem):self.call(self.dad,'notification.subscribe',data)

 def test_one_explicit_grant_can_submit_only_one_generic_notification(self):
  self.prepare(count=2);push.dispatch();push.dispatch();self.assertEqual(self.send.call_count,1)
  self.assertEqual(self.deliveries()[0]['status'],'sent')
  with d.transaction() as c:self.assertEqual([s['status'] for s in d.rows(c,'subscription',self.fid)],['used'])
  self.assertEqual(len([n for n in self.snap(self.dad)['notifications'] if n.get('managed_reminder')]),2)

 def test_ambiguous_timeout_is_unknown_and_never_auto_retries(self):
  self.prepare();self.send.side_effect=TimeoutError('synthetic timeout');push.dispatch();push.dispatch();self.assertEqual(self.send.call_count,1);self.assertEqual(self.deliveries()[0]['status'],'unknown')

 def test_platform_rejection_is_not_reported_as_success_and_station_notice_remains(self):
  self.prepare();self.send.return_value={'errcode':43101};push.dispatch();self.assertEqual(self.deliveries()[0]['status'],'failed')
  self.assertEqual(self.deliveries()[0]['platform_code'],43101);self.assertTrue(any(n.get('managed_reminder') for n in self.snap(self.dad)['notifications']))

 def test_policy_is_rechecked_after_token_lookup_before_sending(self):
  self.prepare()
  def token():self.call(self.dad,'notification.preferences',{'external_enabled':False});return 'token'
  self.token.side_effect=token;push.dispatch();self.send.assert_not_called();self.assertEqual(self.deliveries(),[])

 def test_failed_token_lookup_never_consumes_subscription_or_logs_secrets(self):
  self.prepare();self.token.side_effect=TimeoutError('synthetic token unavailable');push.dispatch();self.send.assert_not_called();self.assertEqual(self.deliveries()[0]['reason'],'token_unavailable')
  with d.transaction() as c:self.assertEqual(d.rows(c,'subscription',self.fid)[0]['status'],'available')
  self.assertNotIn('synthetic-secret',json.dumps(self.deliveries()))

 def test_concurrent_workers_never_reuse_a_one_shot_grant(self):
  self.prepare(count=2);errors=[]
  def run():
   try:push.dispatch()
   except Exception as error:errors.append(error)
  workers=[threading.Thread(target=run) for _ in range(2)]
  for worker in workers:worker.start()
  for worker in workers:worker.join()
  self.assertEqual(errors,[]);self.assertEqual(self.send.call_count,1)

 def test_late_platform_result_cannot_recreate_deleted_persons_delivery(self):
  self.prepare(self.mom)
  def send(*args):
   with d.transaction() as c:d.delete_account(c,self.mom,'long-password-123')
   return {'errcode':0}
  self.send.side_effect=send;push.dispatch()
  with d.transaction() as c:
   self.assertFalse(any(row.get('user_id')==self.mom for kind in ('external_delivery','subscription') for row in d.rows(c,kind)))

 def test_transport_payload_contains_generic_text_without_family_dates_or_health(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):return False
   def read(self,size):return b'{"errcode":0}'
  with patch.object(push.urllib.request,'urlopen',return_value=Response()) as remote:REAL_SEND('synthetic-token','synthetic-openid')
  request=remote.call_args.args[0];body=json.loads(request.data)
  self.assertEqual(body['data'],{'thing1':{'value':'你有待查看事项'}});self.assertEqual(body['page'],'pages/main/main')
  self.assertNotIn(self.fid,json.dumps(body));self.assertNotIn('不出站的私密标题',json.dumps(body,ensure_ascii=False))

 def test_subscription_command_replay_does_not_replenish_a_revoked_one_shot_grant(self):
  self.identity();data={'accepted':True,'template_id':'synthetic-template','identity_id':self.dad+'-wx'}
  with d.transaction() as c:first=d.command(c,self.dad,self.fid,'notification.subscribe','subscription-key',data)
  self.call(self.mom,'member.leave',{})
  with d.transaction() as c:
   self.assertEqual(d.command(c,self.dad,self.fid,'notification.subscribe','subscription-key',data),first)
   receipts=d.rows(c,'subscription',self.fid);self.assertEqual(len(receipts),1);self.assertEqual(receipts[0]['status'],'revoked')

 def test_interrupted_send_becomes_unknown_and_is_not_claimed_again(self):
  self.prepare()
  with d.transaction() as c:
   n=next(n for n in d.rows(c,'notification',self.fid) if n.get('managed_reminder'))
   d.put(c,'external_delivery',{'id':n['id'],'user_id':self.dad,'status':'processing','created_at':d.now()-121},self.fid)
  push.dispatch();self.send.assert_not_called();self.assertEqual(self.deliveries()[0]['status'],'unknown')

 def test_subscription_is_bound_to_the_exact_verified_wechat_identity(self):
  self.identity()
  with d.transaction() as c:d.put(c,'wechat_identity',{'id':'second-identity','user_id':self.dad,'appid':'synthetic-app','openid':'second-openid'})
  self.call(self.dad,'notification.preferences',{'external_enabled':True});self.call(self.dad,'notification.subscribe',{'accepted':True,'template_id':'synthetic-template','identity_id':'second-identity'})
  self.call(self.dad,'task.create',{'title':'当前身份的提醒','due_date':str(d.today())})
  with patch.object(push,'dispatch'):d.tick()
  push.dispatch();self.send.assert_called_once_with('synthetic-token','second-openid')

 def test_external_attempts_respect_daily_cap_even_for_already_delivered_station_messages(self):
  self.prepare(count=2);self.subscribe();self.call(self.dad,'notification.preferences',{'daily_limit':1})
  push.dispatch();push.dispatch();self.assertEqual(self.send.call_count,1)
  with d.transaction() as c:self.assertEqual(sum(r['status']=='available' for r in d.rows(c,'subscription',self.fid)),1)
