import concurrent.futures
import os
import threading
import unittest
from unittest.mock import patch
import test_domain
import test_payments
import domain as d
import payments as p


class PaymentRecoveryTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 tx=test_payments.PaymentTests.tx

 def order(self,*args,**kwargs):
  o=test_payments.PaymentTests.order(self,*args,**kwargs)
  with d.transaction() as c:o['platform']='weapp';return d.put(c,'order',o,self.fid)

 def refund(self):
  self.order()
  with d.transaction() as c:p.settle(c,'wechat',self.tx(),'paid')
  return p.request_refund(self.dad,'order-test','合成售后')
 def response(self,r):return {'out_trade_no':'order-test','out_refund_no':r['id'],'status':'SUCCESS','amount':{'refund':12800,'total':12800,'currency':'CNY'}}
 def grants(self):
  with d.transaction() as c:return d.rows(c,'entitlement',self.fid)

 def test_provider_transaction_cannot_pay_another_order(self):
  self.order();self.order('second')
  with d.transaction() as c:p.settle(c,'wechat',self.tx(),'first')
  other=self.tx(id='second-event',order='second');other['transaction_id']='tx-order-test'
  with self.assertRaises(d.Problem) as err:
   with d.transaction() as c:p.settle(c,'wechat',other,'second')
  self.assertEqual(err.exception.status,409)
  self.assertFalse(any(e['source_order']=='second' for e in self.grants()))

 def test_boolean_state_and_integer_amount_are_required(self):
  self.order()
  for changed in ({'success':'false'},{'amount_fen':12800.0}):
   with self.assertRaises(d.Problem):
    with d.transaction() as c:p.settle(c,'wechat',dict(self.tx(),**changed),'bad')
  self.assertEqual(self.grants(),[])

 def test_wrong_wechat_app_query_does_not_grant(self):
  self.order()
  response={'mchid':'merchant','appid':'wrong-app','out_trade_no':'order-test','transaction_id':'tx','trade_state':'SUCCESS','amount':{'total':12800,'currency':'CNY'}}
  with patch.dict(os.environ,{'WX_MCH_ID':'merchant','WX_APP_ID':'correct-app'}),patch.object(p,'adapter',return_value=response):
   with self.assertRaises(d.Problem):p.query(self.dad,'order-test')
  self.assertEqual(self.grants(),[])

 def test_fractional_cent_alipay_amount_is_rejected(self):
  self.order()
  with d.transaction() as c:
   o=d.get(c,'order','order-test');o.update(provider='alipay',platform='app');d.put(c,'order',o,self.fid)
  response={'out_trade_no':'order-test','seller_id':'seller','trade_no':'tx','trade_status':'TRADE_SUCCESS','total_amount':'128.001'}
  with patch.dict(os.environ,{'ALIPAY_SELLER_ID':'seller'}),patch.object(p,'adapter',return_value=response):
   with self.assertRaises(d.Problem):p.query(self.dad,'order-test')
  self.assertEqual(self.grants(),[])

 def test_invalid_refund_response_does_not_revoke(self):
  r=self.refund();response=self.response(r);response['amount']['refund']=1
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.dad}),patch.object(p,'adapter',return_value=response):
   with self.assertRaises(d.Problem):p.approve_refund(self.dad,r['id'])
  self.assertTrue(all(e['status']!='revoked' for e in self.grants()))
  with d.transaction() as c:self.assertEqual(d.get(c,'refund',r['id'])['status'],'processing')

 def test_unknown_refund_outcome_retries_identical_identifier(self):
  r=self.refund()
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.dad}),patch.object(p,'adapter',side_effect=[d.Problem(503,'合成超时'),self.response(r)]) as adapter:
   with self.assertRaises(d.Problem):p.approve_refund(self.dad,r['id'])
   self.assertEqual(p.approve_refund(self.dad,r['id'])['status'],'succeeded')
   self.assertEqual([x.args[0]['refund']['id'] for x in adapter.call_args_list],[r['id'],r['id']])
  self.assertTrue(all(e['status']=='revoked' for e in self.grants()))

 def test_refund_completion_and_query_are_idempotent(self):
  r=self.refund()
  with d.transaction() as c:p.finish_refund(c,r['id']);before=d.get(c,'refund',r['id'])
  with d.transaction() as c:
   p.finish_refund(c,r['id']);self.assertEqual(d.get(c,'refund',r['id'])['version'],before['version'])
   self.assertEqual(c.execute("SELECT count(*) FROM events WHERE type='refund.completed'").fetchone()[0],1)
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.dad}),patch.object(p,'adapter') as adapter:
   self.assertEqual(p.query_refund(self.dad,r['id'])['status'],'succeeded');self.assertEqual(p.approve_refund(self.dad,r['id'])['status'],'succeeded');adapter.assert_not_called()

 def test_late_payment_callback_does_not_restore_refunded_entitlements(self):
  r=self.refund()
  with d.transaction() as c:
   p.finish_refund(c,r['id']);p.settle(c,'wechat',self.tx(id='late-notification'),'late')
   self.assertEqual(d.get(c,'order','order-test')['status'],'refunded')
  self.assertTrue(all(e['status']=='revoked' for e in self.grants()))

 def test_concurrent_refund_completion_writes_one_audit(self):
  r=self.refund();barrier=threading.Barrier(2)
  def adapter(data):barrier.wait(timeout=5);return self.response(r)
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.dad}),patch.object(p,'adapter',side_effect=adapter):
   with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results=list(pool.map(lambda _:p.approve_refund(self.dad,r['id']),range(2)))
  self.assertTrue(all(x['status']=='succeeded' for x in results))
  with d.transaction() as c:self.assertEqual(c.execute("SELECT count(*) FROM events WHERE type='refund.completed'").fetchone()[0],1)

 def test_money_parser_rejects_nonfinite_and_subcent_values(self):
  for value in ('NaN','Infinity','-1','1.001',None):
   with self.subTest(value=value),self.assertRaises(d.Problem):p.money_fen(value)
  self.assertEqual(p.money_fen('128.00'),12800)

 def test_new_order_uses_server_price_and_application_binding(self):
  env={'PAYMENTS_ENABLED':'1','WX_APP_ID':'server-app','WX_MCH_ID':'server-merchant'}
  with patch.dict(os.environ,env),patch.object(p,'configured',return_value=True),patch.object(p,'adapter',return_value={'synthetic':'prepay'}):
   result=p.create(self.dad,{'family_id':self.fid,'provider':'wechat','platform':'weapp','sku':'core','request_id':'new','amount_fen':1,'app_id':'client-forged'})
  with d.transaction() as c:o=d.get(c,'order',result['order_id'])
  self.assertEqual(o['amount_fen'],12800);self.assertEqual(o['app_id'],'server-app');self.assertEqual(o['merchant_id'],'server-merchant')
  tx=dict(self.tx(order=o['id']),app_id='wrong-app',merchant_id='server-merchant')
  with self.assertRaises(d.Problem):
   with d.transaction() as c:p.settle(c,'wechat',tx,'wrong-binding')
  self.assertEqual(self.grants(),[])

 def test_wechat_query_can_settle_late_payment_once(self):
  self.order()
  with d.transaction() as c:o=d.get(c,'order','order-test');o['expires_at']=d.now()-100;d.put(c,'order',o,self.fid)
  response={'mchid':'merchant','appid':'correct-app','out_trade_no':'order-test','transaction_id':'late-tx','trade_state':'SUCCESS','amount':{'total':12800,'currency':'CNY'}}
  with patch.dict(os.environ,{'WX_MCH_ID':'merchant','WX_APP_ID':'correct-app'}),patch.object(p,'adapter',return_value=response) as adapter:
   self.assertEqual(p.query(self.dad,'order-test')['status'],'paid');self.assertEqual(p.query(self.dad,'order-test')['status'],'paid');self.assertEqual(adapter.call_count,1)
  self.assertEqual(len(self.grants()),2)

 def test_alipay_refund_verifies_transaction_request_and_amount(self):
  order={'id':'alipay-order','provider':'alipay','transaction_id':'alipay-tx'};refund={'id':'refund-id','amount_fen':12800}
  response={'out_trade_no':order['id'],'trade_no':order['transaction_id'],'out_request_no':refund['id'],'refund_amount':'128.00','refund_status':'REFUND_SUCCESS'}
  self.assertTrue(p.refund_success(order,refund,response,query=True))
  for change in ({'trade_no':'wrong'},{'out_request_no':'wrong'},{'refund_amount':'0.01'}):
   with self.assertRaises(d.Problem):p.refund_success(order,refund,dict(response,**change),query=True)

 def test_adapter_timeout_returns_queryable_failure(self):
  with patch.object(p.subprocess,'run',side_effect=p.subprocess.TimeoutExpired('synthetic-node',30)):
   with self.assertRaises(d.Problem) as err:p.adapter({})
  self.assertEqual(err.exception.status,503)
