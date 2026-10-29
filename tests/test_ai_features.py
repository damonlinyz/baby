import json
import os
import unittest
from unittest.mock import patch
import test_ai
import domain as d
import server
import ai_features as features
import briefings
import content_library


class AiFeatureTests(unittest.TestCase):
 setUp=test_ai.AiReliabilityTests.setUp
 tearDown=test_ai.AiReliabilityTests.tearDown
 call=test_ai.AiReliabilityTests.call
 snap=test_ai.AiReliabilityTests.snap
 response=test_ai.AiReliabilityTests.response
 def generate(self):
  with d.transaction() as c:return briefings.generate(c,self.fid,self.dad,True)
 def test_history_is_own_only_and_survives_expired_entitlements(self):
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()):server.ask_ai(self.dad,self.fid,'如何安排家务','private-history')
  with d.transaction() as c:
   self.assertEqual(features.history(c,self.fid,self.mom)['items'],[])
   history=features.history(c,self.fid,self.dad)['items'];self.assertEqual(history[0]['question'],'如何安排家务')
   self.assertIn('每人认领',history[0]['result']['answer'])
   self.assertIn('latency_ms',d.rows(c,'ai_usage',self.fid)[0])
 def test_source_withdrawal_hides_answer_and_deleting_does_not_refund_quota(self):
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()):server.ask_ai(self.dad,self.fid,'如何安排家务','private-history')
  with d.transaction() as c:
   u=d.rows(c,'ai_usage',self.fid)[0];k=d.get(c,'knowledge',u['result']['citations'][0]['id']);k['status']='retired';d.put(c,'knowledge',k)
   item=features.history(c,self.fid,self.dad)['items'][0];self.assertIsNone(item['result']);self.assertEqual(item['status'],'source_changed')
   with self.assertRaises(d.Problem):features.delete_history(c,self.fid,self.mom,{'id':item['id']})
   features.delete_history(c,self.fid,self.dad,{'id':item['id']});self.assertEqual(features.history(c,self.fid,self.dad)['items'],[])
   self.assertEqual(d.get(c,'ai_usage',item['id'])['status'],'committed')
  with self.assertRaises(d.Problem) as e:server.ask_ai(self.dad,self.fid,'如何安排家务','private-history')
  self.assertEqual(e.exception.status,409)
 def test_retention_clears_question_and_answer_without_altering_accounting(self):
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()):server.ask_ai(self.dad,self.fid,'如何安排家务','private-history')
  with d.transaction() as c:
   u=d.rows(c,'ai_usage',self.fid)[0];u['created_at']=d.now()-181*86400;d.put(c,'ai_usage',u,self.fid);features.clean(c)
   u=d.get(c,'ai_usage',u['id']);self.assertNotIn('question',u);self.assertNotIn('result',u);self.assertEqual(u['status'],'committed')
 def test_brief_enhancement_never_sends_private_fields_and_replay_uses_one_call(self):
  t=self.snap(self.dad)['tasks'][0];self.call(self.dad,'task.update',{'id':t['id'],'version':t['version'],'title':'私人姓名与日期 secret-family-123'})
  id=self.generate()
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()) as remote:
   first=features.enhance(self.dad,self.fid,{'id':id,'confirmed':True});self.assertEqual(first,features.enhance(self.dad,self.fid,{'id':id,'confirmed':True}))
   self.assertEqual(remote.call_count,1);payload=json.loads(remote.call_args[0][0].data)
   self.assertNotIn('secret-family-123',json.dumps(payload,ensure_ascii=False));self.assertNotIn(self.fid,json.dumps(payload))
  with d.transaction() as c:
   self.assertEqual(features.history(c,self.fid,self.dad)['items'],[])
   self.assertEqual(briefings.read(c,self.fid,self.dad,id)['enhancement'],first['result'])
 def test_midflight_snapshot_change_discards_enhancement(self):
  id=self.generate()
  def update():
   with d.transaction() as c:briefings.generate(c,self.fid,self.dad,True)
  with patch.object(server.urllib.request,'urlopen',return_value=self.response(update)):
   with self.assertRaises(d.Problem):features.enhance(self.dad,self.fid,{'id':id,'confirmed':True})
  with d.transaction() as c:self.assertIsNone(features.enhanced(c,self.fid,self.dad,id));self.assertEqual(d.rows(c,'ai_usage',self.fid)[0]['status'],'released')
 def test_automatic_enhancement_requires_consent_and_is_cancelled_by_optout(self):
  id=self.generate()
  with patch.object(server.urllib.request,'urlopen') as remote:features.dispatch();remote.assert_not_called()
  self.call(self.dad,'ai.briefing_preferences',{'enabled':True,'confirmed':True})
  def optout():self.call(self.dad,'ai.briefing_preferences',{'enabled':False})
  with patch.object(server.urllib.request,'urlopen',return_value=self.response(optout)) as remote:features.dispatch();self.assertEqual(remote.call_count,1)
  with d.transaction() as c:self.assertIsNone(features.enhanced(c,self.fid,self.dad,id))
 def test_briefing_quota_is_independent_from_qa_and_failures_do_not_consume(self):
  os.environ['SERVICE_MODE']='paid'
  with d.transaction() as c:
   d.put(c,'entitlement',{'id':'core','benefit':'core','status':'active','owner':self.dad},self.fid)
   d.put(c,'entitlement',{'id':'ai','benefit':'ai','owner':self.dad,'status':'active','starts_at':d.now()-1,'ends_at':d.now()+600,'quota':1,'briefing_quota':1},self.fid)
  id=self.generate()
  with patch.object(server.urllib.request,'urlopen',return_value=self.response()):
   server.ask_ai(self.dad,self.fid,'如何安排家务','quota-qa');features.enhance(self.dad,self.fid,{'id':id,'confirmed':True})
   with d.transaction() as c:briefings.generate(c,self.fid,self.dad,True)
   with self.assertRaises(d.Problem) as e:features.enhance(self.dad,self.fid,{'id':id,'confirmed':True})
   self.assertEqual(e.exception.status,402)
 def test_safety_routes_normalized_high_risk_inputs_without_remote_calls(self):
  with patch.object(server.urllib.request,'urlopen') as remote:
   for index,question in enumerate(('我不想活了','宝宝窒息了','How much medicine dosage?','药要吃多少ｍｇ／ｋｇ')):
    self.assertEqual(server.ask_ai(self.dad,self.fid,question,'safety-'+str(index))['kind'],'safety')
   remote.assert_not_called()


class LibraryTests(unittest.TestCase):
 setUp=test_ai.AiReliabilityTests.setUp
 tearDown=test_ai.AiReliabilityTests.tearDown
 call=test_ai.AiReliabilityTests.call
 snap=test_ai.AiReliabilityTests.snap
 def test_optional_templates_are_84_unique_and_do_not_auto_populate_family(self):
  rows=content_library.catalogue()['templates'];self.assertEqual(len(rows),84);self.assertEqual(len({r['title'] for r in rows}),84)
  self.assertEqual(len(self.snap(self.dad)['tasks']),4)
 def test_selection_is_idempotent_and_preserves_user_edits(self):
  data={'template_id':'1-1','assignee':self.mom,'confirmed':True}
  with d.transaction() as c:
   r=d.command(c,self.dad,self.fid,'task.from_library','one-template',data)
   self.assertEqual(r,d.command(c,self.dad,self.fid,'task.from_library','one-template',data))
   t=d.get(c,'task',r['id']);self.assertIsNone(t['pregnancy_id']);self.assertIsNone(t['due_date']);self.assertEqual(len(t['steps']),3)
   d.mutate(c,self.dad,self.fid,'task.update',{'id':t['id'],'version':t['version'],'steps':['自己的执行安排']})
   d.command(c,self.dad,self.fid,'task.from_library','one-template',data)
   self.assertEqual(d.get(c,'task',r['id'])['steps'],['自己的执行安排'])
 def test_only_coordinator_can_select_for_active_members_and_confirmed(self):
  for user,data in ((self.mom,{'template_id':'1-1','confirmed':True}),(self.dad,{'template_id':'bad','confirmed':True}),(self.dad,{'template_id':'1-1'}),(self.dad,{'template_id':'1-1','confirmed':True,'assignee':self.other})):
   with self.assertRaises(d.Problem):self.call(user,'task.from_library',data)
