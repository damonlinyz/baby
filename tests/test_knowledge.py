import datetime
import json
import os
import unittest
from unittest.mock import patch
import test_domain
import domain as d
import server


class KnowledgeTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 snap=test_domain.DomainTests.snap

 def publish(self,**changes):
  data={'title':'家庭分工','topic':'沟通','body':'商量具体负责人','source':'合成测试生活内容','status':'published',**changes}
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.dad}):
   with d.transaction() as c:
    result=d.publish_knowledge(c,self.dad,data)
    return d.get(c,'knowledge',result['id'])

 def test_published_list_filters_drafts_retired_expired_and_malformed(self):
  valid=self.publish(valid_until=str(d.today()))
  self.publish(status='draft');self.publish(status='retired')
  expired=self.publish(status='draft',valid_until=str(d.today()-datetime.timedelta(days=1)))
  with d.transaction() as c:
   expired['status']='published';d.put(c,'knowledge',expired)
   d.put(c,'knowledge',dict(valid,id='malformed',valid_until='invalid'))
   self.assertEqual([k['id'] for k in d.published_knowledge(c)],[valid['id']])
  self.assertEqual([k['id'] for k in self.snap(self.dad)['knowledge']],[valid['id']])

 def test_versions_are_preserved_and_stale_edit_rejected(self):
  first=self.publish()
  second=self.publish(id=first['id'],version=first['version'],status='retired')
  with d.transaction() as c:
   versions=d.rows(c,'knowledge_version')
   self.assertEqual(len(versions),2)
   self.assertEqual(next(v for v in versions if v['content_version']==1)['status'],'published')
   self.assertEqual(second['content_version'],2)
  with self.assertRaises(d.Problem) as err:self.publish(id=first['id'],version=first['version'])
  self.assertEqual(err.exception.status,409)

 def test_expired_publication_medical_content_and_non_admin_are_rejected(self):
  for changes in ({'valid_until':str(d.today()-datetime.timedelta(days=1))},{'clinical':True}):
   with self.assertRaises(d.Problem):self.publish(**changes)
  with self.assertRaises(d.Problem) as err:
   with d.transaction() as c:d.publish_knowledge(c,self.mom,{})
  self.assertEqual(err.exception.status,403)

 def test_ai_uses_only_current_knowledge_and_no_evidence_does_not_call_model(self):
  k=self.publish(status='retired')
  with patch.object(server,'integrations',return_value={'ai':True}),patch.object(server.urllib.request,'urlopen') as remote:
   result=server.ask_ai(self.dad,self.fid,'如何安排家务','no-evidence')
   remote.assert_not_called();self.assertEqual(result['citations'],[])
  k=self.publish()
  with patch.object(server,'integrations',return_value={'ai':False}):
   result=server.ask_ai(self.dad,self.fid,'如何安排家务','lookup')
   self.assertEqual([ref['id'] for ref in result['citations']],[k['id']])

 def test_withdrawal_during_generation_releases_quota(self):
  k=self.publish()
  class Response:
   def __enter__(inner):
    self.publish(id=k['id'],version=k['version'],status='retired');return inner
   def __exit__(inner,*args):pass
   def read(inner,*args):return json.dumps({'choices':[{'message':{'content':'每人认领一项家务'}}]}).encode()
  env={'AI_BASE_URL':'https://example.invalid/v1','AI_API_KEY':'synthetic','AI_MODEL':'test','SERVICE_MODE':'self_hosted'}
  with patch.dict(os.environ,env),patch.object(server.urllib.request,'urlopen',return_value=Response()):
   with self.assertRaises(d.Problem) as err:server.ask_ai(self.dad,self.fid,'如何安排家务','withdraw-during-generation')
  self.assertEqual(err.exception.status,503)
  with d.transaction() as c:
   self.assertEqual(d.rows(c,'ai_usage',self.fid)[0]['status'],'released')
   self.assertEqual(c.execute('SELECT count(*) FROM requests').fetchone()[0],0)

 def test_cached_answer_is_not_replayed_after_source_withdrawal(self):
  k=self.publish();cached={'answer':'旧答案','kind':'ai','citations':[{'id':k['id'],'version':k['content_version'],'title':k['title']}]}
  with d.transaction() as c:c.execute('INSERT INTO requests VALUES(?,?,?,?)',(self.dad,'ai:cached',d.dumps(cached),d.now()))
  self.publish(id=k['id'],version=k['version'],status='retired')
  with self.assertRaises(d.Problem) as err:server.ask_ai(self.dad,self.fid,'如何安排家务','cached')
  self.assertEqual(err.exception.status,409)
