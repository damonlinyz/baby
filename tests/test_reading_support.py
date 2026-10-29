import datetime as dt
import json
import os
import unittest
from unittest.mock import patch
import test_domain
import domain as d
import knowledge
import support
import operations
import briefings
import ai_features
import maintenance


class ReadingTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def publish(self,**changes):
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.dad}):
   with d.transaction() as c:
    result=d.publish_knowledge(c,self.dad,{'title':'合成生活阅读','body':'一般生活分工内容','topic':'家庭协作','source':'合成编辑','rights':'合成原创','status':'published',**changes})
    return d.get(c,'knowledge',result['id'])
 def join(self):
  inv=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,inv['invite_code'])
 def test_role_and_curated_scope_is_applied_on_snapshot_and_direct_read(self):
  self.join();dad=self.publish(target_roles=['dad']);mom=self.publish(target_roles=['mom']);hidden=self.publish(member_curated=False);shared=self.publish()
  self.assertEqual({k['id'] for k in self.snap(self.other)['knowledge']},{shared['id']})
  self.assertEqual({k['id'] for k in self.snap(self.mom)['knowledge']},{mom['id'],hidden['id'],shared['id']})
  with d.transaction() as c:
   with self.assertRaises(d.Problem):knowledge.read(c,self.fid,self.other,dad['id'])
   self.assertEqual(knowledge.read(c,self.fid,self.other,shared['id'])['knowledge']['rights'],'合成原创')
 def test_regions_are_personal_and_do_not_follow_timezone(self):
  generic=self.publish();local=self.publish(regions=['CN-SH'])
  self.assertEqual(len(self.snap(self.dad)['knowledge']),1)
  self.call(self.dad,'knowledge.preferences',{'region':'CN-SH'})
  self.assertEqual({k['id'] for k in self.snap(self.dad)['knowledge']},{generic['id'],local['id']})
  self.assertEqual([k['id'] for k in self.snap(self.mom)['knowledge']],[generic['id']])
 def test_hidden_health_stage_never_changes_reading_cards_and_grant_is_rechecked(self):
  early=self.publish(stages=['early']);self.call(self.mom,'pregnancy.create',{'consent':True})
  p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+dt.timedelta(days=260))})
  self.assertEqual([k['id'] for k in self.snap(self.mom)['knowledge']],[early['id']]);self.assertEqual(self.snap(self.dad)['knowledge'],[])
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True});self.assertEqual([k['id'] for k in self.snap(self.dad)['knowledge']],[early['id']])
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':False});self.assertEqual(self.snap(self.dad)['knowledge'],[])
  with d.transaction() as c:
   with self.assertRaises(d.Problem):knowledge.read(c,self.fid,self.dad,early['id'])
 def test_ai_retrieval_never_infers_health_stage_from_a_read_grant(self):
  generic=self.publish();self.publish(stages=['early']);self.call(self.mom,'pregnancy.create',{'consent':True})
  p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+dt.timedelta(days=260))})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  with d.transaction() as c:self.assertEqual(len(knowledge.available(c,self.fid,self.dad)),2);self.assertEqual([k['id'] for k in knowledge.for_ai(c,self.fid,self.dad)],[generic['id']])
 def test_valid_from_and_until_boundaries_and_invalid_metadata(self):
  today=str(d.today());future=self.publish(valid_from=str(d.today()+dt.timedelta(days=1)));current=self.publish(valid_from=today,valid_until=today)
  self.assertEqual([k['id'] for k in self.snap(self.dad)['knowledge']],[current['id']])
  bad=[{'target_roles':[]},{'target_roles':['dad','dad']},{'stages':['unknown']},{'regions':['all','CN']},{'regions':['cn']},{'member_curated':1},{'source_url':'javascript:alert(1)'},{'source_url':'https://user:password@example.invalid'},{'source_published_at':str(d.today()+dt.timedelta(days=1))},{'valid_from':today,'valid_until':str(d.today()-dt.timedelta(days=1))}]
  for data in bad:
   with self.subTest(data=data):
    with self.assertRaises(d.Problem):self.publish(**data)
 def test_metadata_versions_are_immutable_and_body_reads_never_reuse_withdrawn_cache(self):
  first=self.publish(source_url='https://example.invalid/source',applicability='一般生活');second=self.publish(id=first['id'],version=first['version'],target_roles=['mom'])
  with d.transaction() as c:
   old=next(k for k in d.rows(c,'knowledge_version') if k['knowledge_id']==first['id'] and k['content_version']==1)
   self.assertEqual(old['target_roles'],['dad','mom','member']);self.assertEqual(second['source_url'],first['source_url'])
   with self.assertRaises(d.Problem):knowledge.read(c,self.fid,self.dad,first['id'])
 def test_region_revoke_then_reselect_does_not_revive_old_brief_payload(self):
  self.publish(regions=['CN-SH']);self.call(self.dad,'knowledge.preferences',{'region':'CN-SH'})
  with d.transaction() as c:id=briefings.generate(c,self.fid,self.dad,True)
  self.call(self.dad,'knowledge.preferences',{'region':''});self.call(self.dad,'knowledge.preferences',{'region':'CN-SH'})
  with d.transaction() as c:self.assertIsNone(d.get(c,'briefing',id)['payload']);self.assertEqual(briefings.read(c,self.fid,self.dad,id)['status'],'invalid')
 def test_usage_counts_reservations_by_purpose_without_private_text(self):
  with d.transaction() as c:
   d.put(c,'entitlement',{'id':'ai-period','benefit':'ai','owner':self.dad,'bound_family':self.fid,'status':'active','quota':3,'briefing_quota':1,'starts_at':d.now()-1,'ends_at':d.now()+100},self.fid)
   for id,user,status,purpose,end in [('mine',self.dad,'committed','qa',0),('pending',self.mom,'reserved','briefing',100),('expired',self.dad,'reserved','qa',-1),('failed',self.dad,'released','qa',100)]:
    d.put(c,'ai_usage',{'id':id,'user_id':user,'status':status,'purpose':purpose,'created_at':d.now(),'reserved_until':d.now()+end,'entitlement_id':'ai-period','question':'不应泄露的问题','result':{'answer':'不应泄露的回答'}},self.fid)
   r=ai_features.usage(c,self.fid,self.dad);self.assertNotIn('不应泄露',d.dumps(r));self.assertEqual(r['my_successful'],1);self.assertEqual(r['my_reserved'],0);self.assertEqual(r['daily']['used'],1)
   self.assertEqual(r['cycles'][0]['counts']['qa']['remaining'],2);self.assertEqual(r['cycles'][0]['counts']['briefing']['reserved'],1)
 def test_expired_period_and_gift_activation_metadata_are_truthful(self):
  with d.transaction() as c:
   d.put(c,'entitlement',{'id':'gift','benefit':'ai_gift','owner':self.dad,'status':'unactivated','quota':300},self.fid)
   d.put(c,'entitlement',{'id':'old','benefit':'ai','owner':self.dad,'status':'active','quota':3,'ends_at':d.now()-1},self.fid)
   r=ai_features.usage(c,self.fid,self.dad);self.assertTrue(next(e for e in r['cycles'] if e['id']=='gift')['can_activate']);self.assertEqual(next(e for e in r['cycles'] if e['id']=='old')['state'],'expired')
 def test_ai_reader_scope_change_during_generation_withholds_answer_and_releases_quota(self):
  import server
  self.publish(regions=['CN-SH']);self.call(self.dad,'knowledge.preferences',{'region':'CN-SH'})
  owner=self
  class Response:
   def __enter__(self):owner.call(owner.dad,'knowledge.preferences',{'region':''});return self
   def __exit__(self,*args):pass
   def read(self,*args):return json.dumps({'choices':[{'message':{'content':'每人认领一项具体家务'}}]}).encode()
  with patch.dict(os.environ,{'AI_BASE_URL':'https://example.invalid/v1','AI_API_KEY':'synthetic','AI_MODEL':'test','SERVICE_MODE':'self_hosted'}),patch.object(server.urllib.request,'urlopen',return_value=Response()):
   with self.assertRaises(d.Problem):server.ask_ai(self.dad,self.fid,'如何安排家务','changed-reading')
  with d.transaction() as c:self.assertEqual(d.rows(c,'ai_usage',self.fid)[0]['status'],'released');self.assertEqual(ai_features.usage(c,self.fid,self.dad)['my_successful'],0)


class SupportTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 def create(self,c,**changes):return support.create(c,self.mom,{'category':'ai','title':'合成使用问题','body':'仅本人手动描述','confirmed':True,'request_id':'same-ticket',**changes})
 def test_create_is_user_scoped_idempotent_and_does_not_attach_family_or_ai_data(self):
  with d.transaction() as c:
   first=self.create(c,family_id=self.fid,question='不应存储的对话',pregnancy_id='不应存储的健康标识');self.assertEqual(first,self.create(c))
   row=support.own(c,self.mom)['tickets'][0];self.assertNotIn('input_hash',row);self.assertNotIn('family_id',row);self.assertNotIn('question',row);self.assertNotIn('不应存储',d.dumps(row))
   self.assertEqual(support.own(c,self.dad)['tickets'],[])
   with self.assertRaises(d.Problem):self.create(c,title='改变参数')
 def test_explicit_consent_and_creation_limit_are_enforced(self):
  with d.transaction() as c:
   with self.assertRaises(d.Problem):self.create(c,confirmed=False)
   for i in range(5):self.create(c,request_id=str(i))
   with self.assertRaises(d.Problem) as e:self.create(c,request_id='six')
   self.assertEqual(e.exception.status,429)
 def test_only_system_admin_replies_and_withdrawal_clears_both_sides(self):
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.other}),d.transaction() as c:
   self.create(c);row=support.own(c,self.mom)['tickets'][0]
   with self.assertRaises(d.Problem):support.admin_list(c,self.dad)
   with self.assertRaises(d.Problem):support.reply(c,self.dad,{'id':row['id'],'version':row['version'],'status':'resolved','reply':'越权回复'})
   support.reply(c,self.other,{'id':row['id'],'version':row['version'],'status':'resolved','reply':'管理员已处理'})
   latest=support.own(c,self.mom)['tickets'][0];self.assertEqual(latest['reply'],'管理员已处理')
   with self.assertRaises(d.Problem):support.withdraw(c,self.dad,latest)
   support.withdraw(c,self.mom,latest);self.assertEqual(support.admin_list(c,self.other)['tickets'],[])
   row=support.own(c,self.mom)['tickets'][0];self.assertEqual((row['body'],row['title'],row['reply']),('','',''))
   with self.assertRaises(d.Problem):self.create(c)
 def test_late_reply_cannot_recreate_withdrawn_content_and_stale_version_rejected(self):
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.other}),d.transaction() as c:
   self.create(c);row=support.own(c,self.mom)['tickets'][0];support.withdraw(c,self.mom,row)
   with self.assertRaises(d.Problem):support.reply(c,self.other,{'id':row['id'],'version':row['version'],'status':'resolved','reply':'迟到的回复'})
   self.assertEqual(d.get(c,'support_ticket',row['id'])['body'],'')
 def test_retention_and_account_deletion_remove_private_ticket_text(self):
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.other}),d.transaction() as c:
   self.create(c);row=d.rows(c,'support_ticket')[0];row['updated_at']=d.now()-181*86400;d.put(c,'support_ticket',row)
   self.assertEqual(support.admin_list(c,self.other)['tickets'],[]);self.assertEqual(support.own(c,self.mom)['tickets'][0]['status'],'expired')
   d.delete_account(c,self.mom,'long-password-123');self.assertEqual(d.rows(c,'support_ticket'),[])
 def test_restore_cannot_revive_prewithdrawal_ticket_body(self):
  import sqlite3
  from pathlib import Path
  with d.transaction() as c:self.create(c)
  snapshot=Path(self.tmp.name)/'snapshot.sqlite3';output=Path(self.tmp.name)/'restored.sqlite3';maintenance.backup(d.DB,snapshot)
  with d.transaction() as c:support.withdraw(c,self.mom,support.own(c,self.mom)['tickets'][0])
  maintenance.restore(snapshot,d.deletion_ledger_path(),output)
  with sqlite3.connect(output) as c:self.assertEqual(c.execute("SELECT count(*) FROM records WHERE kind='support_ticket'").fetchone()[0],0)


class OperationsTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 def failed(self):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  with d.transaction() as c:
   p=d.rows(c,'pregnancy',self.fid)[0]
   return d.put(c,'outbox',{'id':'failed-timeline','pregnancy_id':p['id'],'pregnancy_revision':p['revision'],'recipients':[self.mom],'event_id':'event','status':'failed','attempts':3,'created_at':d.now(),'available_at':0,'private':'不应显示的私密日期'},self.fid)
 def test_overview_is_admin_only_and_projects_no_health_or_question_text(self):
  self.failed()
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.other}),d.transaction() as c:
   with self.assertRaises(d.Problem):operations.overview(c,self.dad)
   r=operations.overview(c,self.other);self.assertNotIn('不应显示',d.dumps(r));self.assertNotIn('recipients',d.dumps(r));self.assertTrue(r['queues'][0]['retryable'])
 def test_failed_station_retry_is_idempotent_and_still_delivers_only_once(self):
  row=self.failed();data={'id':row['id'],'version':row['version'],'kind':'outbox','confirmed':True,'request_id':'retry-one'}
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.other}),d.transaction() as c:
   self.assertEqual(operations.retry(c,self.other,data),{'status':'pending'});self.assertEqual(operations.retry(c,self.other,dict(reversed(list(data.items())))),{'status':'pending'})
   d.process_outbox(c,self.fid);d.process_outbox(c,self.fid)
   self.assertEqual(len([r for r in d.rows(c,'notification',self.fid) if r['id'].startswith('failed-timeline')]),1)
   self.assertEqual(d.get(c,'outbox',row['id'])['manual_retries'],1)
   self.assertEqual(c.execute("SELECT count(*) FROM events WHERE type='operations.retry' AND family_id=?",(self.fid,)).fetchone()[0],0)
 def test_stale_source_is_cancelled_instead_of_requeued(self):
  row=self.failed()
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.other}),d.transaction() as c:
   p=d.get(c,'pregnancy',row['pregnancy_id']);p['paused']=True;d.put(c,'pregnancy',p,self.fid)
   result=operations.retry(c,self.other,{'id':row['id'],'version':row['version'],'kind':'outbox','confirmed':True,'request_id':'cancel-stale'})
   self.assertEqual(result,{'status':'cancelled'});self.assertEqual(d.get(c,'outbox',row['id'])['status'],'cancelled')
 def test_external_unknown_and_ai_can_never_be_force_retried(self):
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.other}),d.transaction() as c:
   for kind in ('external_delivery','ai_usage','reminder'):
    with self.assertRaises(d.Problem) as e:operations.retry(c,self.other,{'kind':kind,'request_id':'not-allowed'})
    self.assertEqual(e.exception.status,400)
 def test_stale_version_and_manual_round_limit_are_checked(self):
  row=self.failed()
  with patch.dict(os.environ,{'ADMIN_USER_ID':self.other}),d.transaction() as c:
   data={'id':row['id'],'version':row['version'],'kind':'outbox','confirmed':True,'request_id':'first'}
   with self.assertRaises(d.Problem):operations.retry(c,self.other,dict(data,version=999))
   row['manual_retries']=3;d.put(c,'outbox',row,self.fid)
   with self.assertRaises(d.Problem):operations.retry(c,self.other,dict(data,version=d.get(c,'outbox',row['id'])['version']))
