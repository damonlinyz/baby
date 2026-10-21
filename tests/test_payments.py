import unittest,sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import domain as d,payments
import test_domain
class PaymentTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 def order(self,id='order-test',amount=12800,sku='core'):
  with d.transaction() as c:
   return d.put(c,'order',{'id':id,'family_id':self.fid,'payer':self.dad,'sku':sku,'provider':'wechat','amount_fen':amount,'currency':'CNY','status':'pending','policy_version':'1.0','expires_at':d.now()+1800},self.fid)
 def tx(self,id='event-test',order='order-test',amount=12800):return {'event_id':id,'transaction_id':'tx-'+order,'order_id':order,'amount_fen':amount,'currency':'CNY','success':True}
 def test_duplicate_callback_grants_once(self):
  self.order()
  with d.transaction() as c:payments.settle(c,'wechat',self.tx(),'same-hash')
  with d.transaction() as c:payments.settle(c,'wechat',self.tx(),'same-hash')
  with d.transaction() as c:
   self.assertEqual(len(d.rows(c,'entitlement',self.fid)),2);self.assertEqual(d.get(c,'order','order-test')['status'],'paid')
 def test_wrong_amount_never_grants(self):
  self.order()
  with self.assertRaises(d.Problem):
   with d.transaction() as c:payments.settle(c,'wechat',self.tx(amount=1),'wrong')
  with d.transaction() as c:self.assertEqual(d.rows(c,'entitlement',self.fid),[])
 def test_refund_revokes_only_source_order(self):
  self.order();self.order('order-two',2500,'ai30')
  with d.transaction() as c:
   payments.settle(c,'wechat',self.tx(),'hash1');payments.settle(c,'wechat',self.tx(id='event-two',order='order-two',amount=2500),'hash2')
  refund=payments.request_refund(self.dad,'order-test','测试环境退款')
  with d.transaction() as c:
   payments.finish_refund(c,refund['id']);grants=d.rows(c,'entitlement',self.fid)
   self.assertTrue(all(e['status']=='revoked' for e in grants if e['source_order']=='order-test'))
   self.assertTrue(all(e['status']=='active' for e in grants if e['source_order']=='order-two'))
 def test_no_fake_payment_when_disabled(self):
  with self.assertRaises(d.Problem):payments.create(self.dad,{'family_id':self.fid,'provider':'wechat','platform':'weapp','sku':'core','request_id':'req'})
  with d.transaction() as c:self.assertEqual(d.rows(c,'order',self.fid),[])
