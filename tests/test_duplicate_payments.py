"""Synthetic settlement only: these tests never call a real payment provider."""
import concurrent.futures
import os
import threading
import unittest
from unittest.mock import patch
import test_domain
import test_payments
import domain as d
import payments as p


class DuplicateCorePaymentTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 def order(self,*args,**kwargs):
  o=test_payments.PaymentTests.order(self,*args,**kwargs)
  with d.transaction() as c:o['platform']='weapp';return d.put(c,'order',o,self.fid)
 tx=test_payments.PaymentTests.tx

 def settle(self,order,event=None):
  with d.transaction() as c:p.settle(c,'wechat',self.tx(id=event or 'event-'+order,order=order),'hash-'+(event or order))

 def records(self,kind):
  with d.transaction() as c:return d.rows(c,kind,self.fid)

 def two_payments(self):
  self.order('first');self.order('second');self.settle('first');self.settle('second')

 def test_late_second_receipt_creates_one_refund_without_grants(self):
  self.order('first');self.order('second')
  with d.transaction() as c:
   o=d.get(c,'order','second');o['expires_at']=d.now()-100;d.put(c,'order',o,self.fid)
  with patch.object(p,'adapter') as adapter:self.settle('first');self.settle('second');adapter.assert_not_called()
  with d.transaction() as c:
   second=d.get(c,'order','second');self.assertEqual(second['status'],'paid');self.assertEqual(second['fulfillment_status'],'duplicate_core');self.assertEqual(second['duplicate_of_order_id'],'first')
  self.assertEqual(len(self.records('entitlement')),2)
  r=self.records('refund')[0];self.assertEqual(r['order_id'],'second');self.assertEqual(r['status'],'requested');self.assertEqual(r['amount_fen'],12800)
  self.assertEqual(len(self.records('notification')),1)

 def test_replays_and_query_event_do_not_duplicate_case(self):
  self.two_payments();self.settle('second');self.settle('second','different-channel-event')
  self.assertEqual(len(self.records('refund')),1);self.assertEqual(len(self.records('notification')),1);self.assertEqual(len(self.records('entitlement')),2)
  with d.transaction() as c:self.assertEqual(c.execute("SELECT count(*) FROM events WHERE type='payment.duplicate_core'").fetchone()[0],1)

 def test_concurrent_core_payments_choose_one_entitlement(self):
  self.order('first');self.order('second');barrier=threading.Barrier(2)
  def submit(order):barrier.wait(timeout=5);self.settle(order)
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(submit,['first','second']))
  grants=self.records('entitlement');refunds=self.records('refund')
  self.assertEqual(len(grants),2);self.assertEqual(len(refunds),1)
  self.assertTrue(all(g['source_order']!=refunds[0]['order_id'] for g in grants))

 def test_cross_provider_duplicate_is_still_detected(self):
  self.order('first');self.order('second');self.settle('first')
  with d.transaction() as c:
   o=d.get(c,'order','second');o['provider']='alipay';d.put(c,'order',o,self.fid);p.settle(c,'alipay',self.tx(id='alipay-notify',order='second'),'alipay-hash')
  self.assertEqual(len(self.records('entitlement')),2);self.assertEqual(len(self.records('refund')),1)

 def test_duplicate_refund_does_not_revoke_original_or_gift(self):
  self.two_payments();r=self.records('refund')[0]
  response={'out_trade_no':'second','out_refund_no':r['id'],'status':'SUCCESS','amount':{'refund':12800,'total':12800,'currency':'CNY'}}
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.dad}),patch.object(p,'adapter',return_value=response):self.assertEqual(p.approve_refund(self.dad,r['id'])['status'],'succeeded')
  self.assertTrue(all(e['status'] in ('active','unactivated') and e['source_order']=='first' for e in self.records('entitlement')))
  self.settle('second','callback-after-refund')
  self.assertEqual(len(self.records('entitlement')),2)
  with d.transaction() as c:self.assertEqual(d.get(c,'order','second')['status'],'refunded')

 def test_uncertain_refund_retries_existing_duplicate_identifier(self):
  self.two_payments();r=self.records('refund')[0]
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.dad}),patch.object(p,'adapter',side_effect=d.Problem(503,'合成网络超时')) as adapter:
   for _ in range(2):
    with self.assertRaises(d.Problem):p.approve_refund(self.dad,r['id'])
   self.assertEqual([call.args[0]['refund']['id'] for call in adapter.call_args_list],[r['id'],r['id']])
  self.assertEqual(self.records('refund')[0]['status'],'processing');self.assertTrue(all(e['status'] in ('active','unactivated') for e in self.records('entitlement')))

 def test_user_reuses_system_case_and_other_user_cannot_access(self):
  self.two_payments();r=self.records('refund')[0];self.assertEqual(p.request_refund(self.dad,'second','查询售后')['id'],r['id'])
  with self.assertRaises(d.Problem):p.request_refund(self.mom,'second','他人申请')
  with d.transaction() as c:
   self.assertEqual(len(d.snapshot(c,self.dad,self.fid)['refunds']),1);self.assertEqual(d.snapshot(c,self.mom,self.fid)['refunds'],[])

 def test_first_refund_before_new_payment_allows_new_buyout(self):
  self.order('first');self.order('second');self.settle('first')
  r=p.request_refund(self.dad,'first','合成首单退款')
  with d.transaction() as c:p.finish_refund(c,r['id'])
  self.settle('second')
  self.assertEqual(len(self.records('refund')),1)
  self.assertEqual(len([e for e in self.records('entitlement') if e['benefit']=='core' and e['status']=='active']),1)
  with d.transaction() as c:self.assertEqual(d.get(c,'order','second')['fulfillment_status'],'granted')

 def test_ai_renewal_is_not_treated_as_duplicate_core(self):
  self.order('first');self.order('ai-one',2500,'ai30');self.order('ai-two',2500,'ai30');self.settle('first')
  for order in ('ai-one','ai-two'):
   with d.transaction() as c:p.settle(c,'wechat',self.tx(id='event-'+order,order=order,amount=2500),'hash-'+order)
  self.assertEqual(self.records('refund'),[])
  packs=sorted([e for e in self.records('entitlement') if e['benefit']=='ai'],key=lambda e:e['starts_at']);self.assertEqual(packs[0]['ends_at'],packs[1]['starts_at'])

 def test_separate_family_buyout_does_not_share_duplicate_case(self):
  self.order('first');self.settle('first')
  with d.transaction() as c:
   other=d.create_family(c,self.dad,{'name':'独立家庭'})['family_id']
   d.put(c,'order',{'id':'other-core','family_id':other,'payer':self.dad,'sku':'core','provider':'wechat','amount_fen':12800,'currency':'CNY','status':'pending','policy_version':'1.0','expires_at':d.now()+1800},other)
   p.settle(c,'wechat',self.tx(order='other-core'),'other-hash');self.assertEqual(len(d.rows(c,'entitlement',other)),2);self.assertEqual(d.rows(c,'refund',other),[])
