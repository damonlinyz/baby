import datetime as dt
import json
import unittest

import test_domain
import domain as d
import task_details


class TaskDetailTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def seed(self):
  with d.transaction() as c:d.seed_knowledge(c)
 def create(self,**changes):return self.call(self.dad,'task.create',dict({'title':'整理物品','steps':['  先盘点  ','再核对缺口'],'knowledge_ids':['base-1']},**changes))['id']
 def task(self,id,user=None):return next(t for t in self.snap(user or self.dad)['tasks'] if t['id']==id)
 def update(self,t,user=None,**changes):return self.call(user or self.dad,'task.update',dict(changes,id=t['id'],version=t['version']))
 def read(self,id,k='base-1',user=None):
  with d.transaction() as c:return task_details.read_knowledge(c,self.fid,user or self.dad,id,k)

 def test_create_normalizes_steps_and_displays_current_reference_provenance(self):
  self.seed();id=self.create();t=self.task(id);self.assertEqual(t['steps'],['先盘点','再核对缺口']);self.assertEqual(t['knowledge_ids'],['base-1'])
  self.assertEqual(t['linked_knowledge'][0]['source'],'产品编辑 · 一般家庭生活内容');self.assertIn('body',self.read(id)['knowledge'])
  self.update(t,steps=[],knowledge_ids=[]);t=self.task(id);self.assertEqual(t['steps'],[]);self.assertEqual(t['linked_knowledge'],[])
  with self.assertRaises(d.Problem):self.read(id)

 def test_invalid_steps_and_links_are_rejected_atomically(self):
  self.seed();id=self.create();before=self.task(id);count=len(self.snap(self.dad)['tasks'])
  bad=[{'steps':v} for v in (None,'步骤',[True],[' '],['x'*201],['步骤']*13)]+[{'knowledge_ids':v} for v in (None,'base-1',[True],['base-1','base-1'],['missing'],['base-'+str(i) for i in range(6)])]
  for changes in bad:
   with self.subTest(changes=changes):
    with self.assertRaises(d.Problem):self.create(**changes)
    with self.assertRaises(d.Problem):self.update(before,**changes)
  self.assertEqual(self.task(id),before);self.assertEqual(len(self.snap(self.dad)['tasks']),count)

 def test_only_coordinator_edits_and_basic_member_reference_access_follows_assignment(self):
  self.seed();invite=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,invite['invite_code'])
  id=self.create(assignee=self.other);t=self.task(id)
  self.assertEqual(self.read(id,user=self.other)['knowledge']['id'],'base-1')
  for user in (self.other,self.mom):
   with self.assertRaises(d.Problem):self.update(t,user,steps=['替换'])
  other=self.create()
  with self.assertRaises(d.Problem):self.read(other,user=self.other)
  self.call(self.dad,'member.remove',{'user_id':self.other})
  with self.assertRaises(d.Problem):self.read(id,user=self.other)

 def test_withdrawn_expired_and_medical_content_cannot_be_linked_or_read(self):
  self.seed();id=self.create()
  for changes in ({'status':'retired'},{'valid_until':str(d.today()-dt.timedelta(days=1))},{'clinical':True}):
   with d.transaction() as c:
    original=d.get(c,'knowledge','base-1');updated=dict(original,**changes);d.put(c,'knowledge',updated)
   self.assertEqual(self.task(id)['linked_knowledge'],[]);self.assertEqual(self.task(id)['knowledge_ids'],[])
   with self.assertRaises(d.Problem):self.read(id)
   with self.assertRaises(d.Problem):self.create()
   with d.transaction() as c:d.put(c,'knowledge',original)
  with d.transaction() as c:
   k=d.get(c,'knowledge','base-1');k['content_version']=2;k['body']='更新后的生活说明';d.put(c,'knowledge',k)
  self.assertEqual(self.read(id)['knowledge']['body'],'更新后的生活说明');self.assertEqual(self.task(id)['linked_knowledge'][0]['content_version'],2)

 def test_pending_detail_edit_invalidates_approval_and_unread_notice(self):
  self.seed();id=self.create();f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
  result=self.update(self.task(id),status='done');a=next(a for a in self.snap(self.mom)['approvals'] if a['id']==result['approval_id'])
  self.update(self.task(id),steps=['核对更新后的缺口']);self.assertEqual(self.task(id)['status'],'in_progress')
  with self.assertRaises(d.Problem):self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  self.assertTrue(all(n.get('cancelled') for n in self.snap(self.mom)['notifications'] if n.get('ref')==a['id']))

 def test_health_revocation_and_cross_family_deny_reference_route(self):
  self.seed();self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy'];id=self.create()
  with d.transaction() as c:
   t=d.get(c,'task',id);t.update(pregnancy_id=p['id'],private_health_anchor=True);d.put(c,'task',t,self.fid)
   other=d.create_family(c,self.dad,{'name':'其他家庭'})['family_id']
  with self.assertRaises(d.Problem):self.read(id)
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True});self.assertEqual(self.read(id)['knowledge']['id'],'base-1')
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':False})
  with self.assertRaises(d.Problem):self.read(id)
  with self.assertRaises(d.Problem):
   with d.transaction() as c:task_details.read_knowledge(c,other,self.dad,id,'base-1')

 def test_baby_reference_requires_its_separate_consent(self):
  self.seed();self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy'];id=self.create()
  import lifecycle
  with d.transaction() as c:
   t=d.get(c,'task',id);t.update(pregnancy_id=p['id'],template_key=next(iter(lifecycle.BABY_TEMPLATE_KEYS)));d.put(c,'task',t,self.fid)
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  with self.assertRaises(d.Problem):self.read(id)
  self.call(self.mom,'consent.update',{'scope':'baby_read','grantee':self.dad,'enabled':True,'guardian_consent':True});self.assertEqual(self.read(id)['knowledge']['id'],'base-1')
  self.call(self.mom,'consent.update',{'scope':'baby_read','grantee':self.dad,'enabled':False})
  with self.assertRaises(d.Problem):self.read(id)

 def test_recurring_details_propagate_but_single_instance_edits_are_retained(self):
  self.seed();id=self.create(repeat='daily',due_date=str(d.today()));s=next(s for s in self.snap(self.dad)['task_series'] if s['id']==id)
  tasks=[t for t in self.snap(self.dad)['tasks'] if t.get('series_id')==id];self.assertTrue(all(t['steps']==['先盘点','再核对缺口'] for t in tasks))
  self.update(tasks[0],steps=['仅本次的安排']);self.call(self.dad,'task_series.update',{'id':id,'version':s['version'],'steps':['以后先询问家人'],'knowledge_ids':['base-0']})
  self.assertEqual(self.task(tasks[0]['id'])['steps'],['仅本次的安排']);self.assertEqual(self.task(tasks[1]['id'])['steps'],['以后先询问家人'])
  self.assertEqual(self.task(tasks[1]['id'])['knowledge_ids'],['base-0'])

 def test_all_generated_life_templates_have_steps_source_and_preserve_completed_content(self):
  self.seed();self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+dt.timedelta(days=260))})
  self.call(self.mom,'consent.update',{'scope':'health_read','grantee':self.dad,'enabled':True})
  tasks=[t for t in self.snap(self.dad)['tasks'] if t.get('template_key')];self.assertEqual(len(tasks),13)
  self.update(tasks[0],status='done');done=self.task(tasks[0]['id']);p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(d.today()+dt.timedelta(days=261))});self.assertEqual(self.task(done['id']),done)
  p=self.snap(self.mom)['pregnancy'];self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':str(d.today()),'babies':1,'guardian_consent':True,'birth_outcomes':['growing']})
  templates=[t for t in self.snap(self.mom)['tasks'] if t.get('template_key')];self.assertEqual(len(templates),22)
  for task in templates:self.assertEqual(task['template_version'],2);self.assertEqual(len(task['steps']),3);self.assertIn('一般生活事务',task['template_source'])

 def test_stale_version_and_replayed_request_never_overwrite_later_details(self):
  self.seed();id=self.create();t=self.task(id);data={'id':id,'version':t['version'],'steps':['第一次修改']}
  with d.transaction() as c:first=d.command(c,self.dad,self.fid,'task.update','detail-key',data)
  self.update(self.task(id),steps=['更新的修改'])
  with d.transaction() as c:self.assertEqual(d.command(c,self.dad,self.fid,'task.update','detail-key',data),first)
  self.assertEqual(self.task(id)['steps'],['更新的修改'])
  with self.assertRaises(d.Problem):self.update(t,steps=['不能覆盖'])

 def test_personal_deletion_leaves_no_extra_private_task_detail_cache(self):
  self.seed();self.call(self.mom,'pregnancy.create',{'consent':True});p=self.snap(self.mom)['pregnancy'];id=self.create(steps=['合成私密步骤'])
  with d.transaction() as c:
   t=d.get(c,'task',id);t.update(pregnancy_id=p['id'],private_health_anchor=True);d.put(c,'task',t,self.fid)
   d.delete_account(c,self.mom,'long-password-123')
  with self.assertRaises(d.Problem):self.read(id)
  self.assertNotIn('合成私密步骤',json.dumps(self.snap(self.dad),ensure_ascii=False))

 def test_customizing_template_steps_keeps_original_provenance_and_does_not_complete_task(self):
  t=self.snap(self.dad)['tasks'][0];self.assertEqual(len(t['steps']),3);self.assertIn('template_source',t)
  self.update(t,steps=['家人自行补充的步骤']);updated=self.task(t['id'])
  self.assertTrue(updated['details_customized']);self.assertEqual(updated['status'],'todo');self.assertEqual(updated['template_source'],t['template_source'])
