import hashlib
import json
import os
import unittest
from unittest.mock import patch
import test_domain
import domain as d
import server


class AiReliabilityTests(unittest.TestCase):
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def setUp(self):
  test_domain.DomainTests.setUp(self)
  with d.transaction() as c:d.seed_knowledge(c)
  env=patch.dict(os.environ,{'AI_BASE_URL':'https://example.invalid/v1','AI_API_KEY':'synthetic','AI_MODEL':'test','SERVICE_MODE':'self_hosted','AI_DAILY_LIMIT':'30'})
  env.start();self.addCleanup(env.stop)

 def response(self,callback=None):
  class Response:
   def __enter__(inner):
    if callback:callback()
    return inner
   def __exit__(inner,*args):pass
   def read(inner,*args):return json.dumps({'choices':[{'message':{'content':'每人认领一项具体家务'}}],'usage':{'total_tokens':10}}).encode()
  return Response()
 def ask(self,key='request',question='如何安排家务',fid=None):return server.ask_ai(self.dad,fid or self.fid,question,key)
 def usage(self):
  with d.transaction() as c:return d.rows(c,'ai_usage',self.fid)
 def join_other(self):
  invite=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,invite['invite_code'])
 def transfer(self):
  with d.transaction() as c:
   tid=d.mutate(c,self.dad,self.fid,'family.transfer',{'user_id':self.other})['transfer_id'];t=d.get(c,'transfer',tid)
   d.mutate(c,self.other,self.fid,'transfer.accept',{'id':tid,'version':t['version']})
 def paid(self):
  os.environ['SERVICE_MODE']='paid'
  with d.transaction() as c:
   d.put(c,'entitlement',{'id':'core','benefit':'core','status':'active'},self.fid)
   d.put(c,'entitlement',{'id':'ai','benefit':'ai','status':'active','starts_at':d.now()-1,'ends_at':d.now()+600,'quota':1},self.fid)

 def test_failed_request_can_retry_and_success_is_counted_once(self):
  with patch.object(server.urllib.request,'urlopen',side_effect=[OSError('synthetic outage'),self.response()]) as remote:
   with self.assertRaises(d.Problem) as err:self.ask()
   self.assertEqual(err.exception.status,503);self.assertEqual(self.usage()[0]['status'],'released')
   first=self.ask();self.assertEqual(first,self.ask());self.assertEqual(remote.call_count,2)
  self.assertEqual(len(self.usage()),1);self.assertEqual(self.usage()[0]['attempts'],2)
  self.assertEqual(self.usage()[0]['status'],'committed')

 def test_request_id_binds_question_and_family(self):
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()) as remote:
   self.ask()
   with d.transaction() as c:other=d.create_family(c,self.dad,{'name':'另一个家'})['family_id']
   for question,fid in (('不同问题',self.fid),('如何安排家务',other)):
    with self.assertRaises(d.Problem) as err:self.ask(question=question,fid=fid)
    self.assertEqual(err.exception.status,409)
   self.assertEqual(remote.call_count,1)

 def test_inflight_retry_does_not_start_second_model_call(self):
  def retry():
   with self.assertRaises(d.Problem) as err:self.ask()
   self.assertEqual(err.exception.status,409)
  with patch.object(server.urllib.request,'urlopen',return_value=self.response(retry)) as remote:
   self.ask();self.assertEqual(remote.call_count,1)

 def test_expired_reservation_is_reclaimed(self):
  id=hashlib.sha256((self.dad+':request').encode()).hexdigest()[:40]
  with d.transaction() as c:d.put(c,'ai_usage',{'id':id,'user_id':self.dad,'input_hash':hashlib.sha256('如何安排家务'.encode()).hexdigest(),'status':'reserved','created_at':d.now()-200,'reserved_until':d.now()-1,'attempts':1,'reservation_id':'old'},self.fid)
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()):self.ask()
  self.assertEqual(self.usage()[0]['status'],'committed');self.assertEqual(self.usage()[0]['attempts'],2)

 def test_old_worker_cannot_release_newer_reservation(self):
  def replace():
   with d.transaction() as c:
    u=d.rows(c,'ai_usage',self.fid)[0];u['reservation_id']='newer-attempt';d.put(c,'ai_usage',u,self.fid)
  with patch.object(server.urllib.request,'urlopen',return_value=self.response(replace)):
   with self.assertRaises(d.Problem):self.ask()
  self.assertEqual(self.usage()[0]['status'],'reserved');self.assertEqual(self.usage()[0]['reservation_id'],'newer-attempt')

 def test_role_change_before_delivery_withholds_answer_and_releases_usage(self):
  self.join_other()
  with patch.object(server.urllib.request,'urlopen',return_value=self.response(self.transfer)):
   with self.assertRaises(d.Problem) as err:self.ask()
   self.assertEqual(err.exception.status,503)
  self.assertEqual(self.usage()[0]['status'],'released');self.assertNotIn('result',self.usage()[0])

 def test_account_deletion_during_request_does_not_resurrect_usage(self):
  self.join_other()
  def remove():
   self.transfer()
   with d.transaction() as c:d.delete_account(c,self.dad,'long-password-123')
  with patch.object(server.urllib.request,'urlopen',return_value=self.response(remove)):
   with self.assertRaises(d.Problem) as err:self.ask()
   self.assertEqual(err.exception.status,503)
  self.assertEqual(self.usage(),[])

 def test_refund_before_delivery_does_not_consume_success_quota(self):
  self.paid()
  def revoke():
   with d.transaction() as c:
    grant=d.get(c,'entitlement','ai');grant['status']='revoked';d.put(c,'entitlement',grant,self.fid)
  with patch.object(server.urllib.request,'urlopen',return_value=self.response(revoke)):
   with self.assertRaises(d.Problem) as err:self.ask()
   self.assertEqual(err.exception.status,503)
  self.assertEqual(self.usage()[0]['status'],'released')

 def test_another_valid_package_is_used_when_first_is_exhausted(self):
  self.paid()
  with d.transaction() as c:
   d.put(c,'ai_usage',{'id':'already-used','user_id':self.dad,'status':'committed','created_at':d.now(),'entitlement_id':'ai'},self.fid)
   d.put(c,'entitlement',{'id':'second-ai','benefit':'ai','status':'active','starts_at':d.now()-1,'ends_at':d.now()+1200,'quota':1},self.fid)
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()):self.ask()
  committed=next(u for u in self.usage() if u['id']!='already-used')
  self.assertEqual(committed['entitlement_id'],'second-ai')

 def test_medical_question_uses_static_route_without_model_or_quota(self):
  with patch.object(server.urllib.request,'urlopen') as remote:
   result=self.ask(question='出血了怎么办');self.assertEqual(result['kind'],'safety');remote.assert_not_called()
  self.assertEqual(self.usage(),[])

 def test_committed_response_is_not_replayed_after_source_withdrawal(self):
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()) as remote:
   self.ask();source=self.usage()[0]['result']['citations'][0]['id']
   with d.transaction() as c:
    k=d.get(c,'knowledge',source);k['status']='retired';d.put(c,'knowledge',k)
   with self.assertRaises(d.Problem) as err:self.ask()
   self.assertEqual(err.exception.status,409);self.assertEqual(remote.call_count,1)
