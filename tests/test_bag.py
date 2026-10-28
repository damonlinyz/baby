import threading
import unittest

import test_domain
import domain as d


class BagTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def item(self, id=None):
  items=self.snap(self.dad)['items']+self.snap(self.dad)['deleted_items']
  return next(i for i in items if i['id']==id) if id else items[0]
 def update(self, item, user=None, **changes):
  return self.call(user or self.dad,'item.update',dict(changes,id=item['id'],version=item['version']))
 def remove(self, item, user=None):
  return self.call(user or self.dad,'item.delete',{'id':item['id'],'version':item['version']})
 def restore(self, item, user=None, **changes):
  return self.call(user or self.dad,'item.restore',dict(changes,id=item['id'],version=item['version']))
 def all_mode(self):
  f=self.snap(self.dad)['family'];self.call(self.dad,'family.update',{'version':f['version'],'mode':'all'})
 def approval(self,id):return next(a for a in self.snap(self.mom)['approvals'] if a['id']==id)

 def test_create_and_edit_all_fields_with_strict_validation(self):
  id=self.call(self.dad,'item.create',{'name':'  入院证件  ','quantity':2,'level':'必备','pack':'证件资料包','assignee':self.mom})['id']
  i=self.item(id);self.assertEqual((i['name'],i['quantity'],i['level']),('入院证件',2,'必备'))
  self.update(i,name='宝宝衣物',quantity=4,level='可选',pack='宝宝用品包',note='按医院清单',assignee=self.dad)
  i=self.item(id);self.assertEqual((i['quantity'],i['level'],i['pack'],i['note'],i['assignee']),(4,'可选','宝宝用品包','按医院清单',self.dad))

 def test_invalid_create_and_edit_are_atomic(self):
  bad=[{'quantity':v} for v in (0,101,1.5,True,'2',None)]+[{'pack':'不存在'},{'level':'紧急'},{'name':' '},{'note':None},{'note':'x'*501},{'assignee':self.other}]
  original=self.item();count=len(self.snap(self.dad)['items'])
  for changes in bad:
   with self.subTest(changes=changes):
    with self.assertRaises(d.Problem):self.call(self.dad,'item.create',dict({'name':'测试'},**changes))
    with self.assertRaises(d.Problem):self.update(original,**changes)
  self.assertEqual(self.item(original['id']),original);self.assertEqual(len(self.snap(self.dad)['items']),count)

 def test_only_coordinator_edits_deletes_restores_and_only_executor_completes(self):
  i=self.item();self.update(i,assignee=self.mom);i=self.item(i['id'])
  for user in (self.mom,self.other):
   with self.assertRaises(d.Problem):self.update(i,user,quantity=3)
   with self.assertRaises(d.Problem):self.remove(i,user)
  with self.assertRaises(d.Problem):self.update(i,status='done')
  with self.assertRaises(d.Problem):self.update(i,assignee=self.dad,status='done')
  self.update(i,self.mom,status='done');self.remove(self.item(i['id']));deleted=self.item(i['id'])
  with self.assertRaises(d.Problem):self.restore(deleted,self.mom)

 def test_basic_member_sees_only_own_items_and_no_deleted_items(self):
  invite=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,invite['invite_code'])
  i=self.item();self.update(i,assignee=self.other);i=self.item(i['id'])
  s=self.snap(self.other);self.assertEqual([x['id'] for x in s['items']],[i['id']]);self.assertEqual(s['deleted_items'],[])
  other=next(x for x in self.snap(self.dad)['items'] if x['id']!=i['id'])
  with self.assertRaises(d.Problem):self.update(other,self.other,status='done')
  with self.assertRaises(d.Problem):self.update(i,self.other,level='必备')
  self.update(i,self.other,status='done');self.remove(self.item(i['id']))
  self.assertEqual(self.snap(self.other)['items'],[]);self.assertEqual(self.snap(self.mom)['deleted_items'],[])

 def test_edit_pending_invalidates_old_approval_and_notification(self):
  self.all_mode();i=self.item();r=self.update(i,status='done');a=self.approval(r['approval_id'])
  self.update(self.item(i['id']),quantity=2);i=self.item(i['id'])
  self.assertEqual(i['status'],'in_progress');self.assertEqual(self.approval(a['id'])['status'],'cancelled')
  with self.assertRaises(d.Problem):self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  self.assertTrue(all(n.get('cancelled') for n in self.snap(self.mom)['notifications'] if n.get('ref')==a['id']))
  new=self.update(i,status='done');a=self.approval(new['approval_id'])
  self.call(self.mom,'approval.decide',{'id':a['id'],'version':a['version'],'decision':'approved'})
  self.assertEqual(self.item(i['id'])['status'],'done')

 def test_noop_edit_keeps_pending_approval_and_version(self):
  self.all_mode();i=self.item();r=self.update(i,status='done');i=self.item(i['id'])
  self.update(i,quantity=i['quantity'],name=i['name']);self.assertEqual(self.item(i['id']),i)
  self.assertEqual(self.approval(r['approval_id'])['status'],'pending')

 def test_edit_completed_resets_completion_until_new_quantity_is_ready(self):
  i=self.item();self.update(i,status='done');i=self.item(i['id']);self.assertTrue(i['completed_at'])
  self.update(i,quantity=3);i=self.item(i['id']);self.assertEqual(i['status'],'todo');self.assertIsNone(i['completed_at'])

 def test_delete_cancels_approval_blocks_edits_and_restore_never_revives_approval(self):
  self.all_mode();i=self.item();r=self.update(i,status='done');i=self.item(i['id']);self.remove(i)
  self.assertNotIn(i['id'],[x['id'] for x in self.snap(self.dad)['items']]);deleted=self.item(i['id'])
  self.assertTrue(deleted['deleted']);self.assertEqual(deleted['status'],'in_progress');self.assertEqual(self.approval(r['approval_id'])['status'],'cancelled')
  for changes in ({'quantity':2},{'status':'done'}):
   with self.assertRaises(d.Problem):self.update(deleted,**changes)
  self.restore(deleted);restored=self.item(i['id']);self.assertFalse(restored['deleted']);self.assertEqual(restored['status'],'in_progress')
  self.assertEqual(self.approval(r['approval_id'])['status'],'cancelled')

 def test_delete_restore_preserves_completed_facts_for_same_executor(self):
  i=self.item();self.update(i,status='done');done=self.item(i['id']);self.remove(done);self.restore(self.item(i['id']))
  restored=self.item(i['id']);self.assertEqual(restored['status'],'done');self.assertEqual(restored['completed_at'],done['completed_at'])

 def test_departed_assignee_requires_explicit_reassignment_before_restore(self):
  i=self.item();self.update(i,assignee=self.mom);self.remove(self.item(i['id']));self.call(self.mom,'member.leave',{});i=self.item(i['id'])
  with self.assertRaises(d.Problem):self.restore(i)
  self.restore(i,assignee=self.dad);self.assertEqual(self.item(i['id'])['assignee'],self.dad)

 def test_stale_versions_and_cross_family_operations_are_rejected(self):
  i=self.item();self.update(i,quantity=2)
  for action in ('item.update','item.delete','item.restore'):
   with self.assertRaises(d.Problem):self.call(self.dad,action,{'id':i['id'],'version':i['version']})
  with d.transaction() as c:fid=d.create_family(c,self.other,{'name':'别的家庭'})['family_id']
  for action in ('item.update','item.delete','item.restore'):
   with self.assertRaises(d.Problem):
    with d.transaction() as c:d.mutate(c,self.other,fid,action,{'id':i['id'],'version':i['version']+1})

 def test_delete_restore_command_replay_never_undoes_later_actions(self):
  i=self.item();data={'id':i['id'],'version':i['version']}
  with d.transaction() as c:d.command(c,self.dad,self.fid,'item.delete','delete-key',data)
  self.restore(self.item(i['id']))
  with d.transaction() as c:d.command(c,self.dad,self.fid,'item.delete','delete-key',data)
  self.assertFalse(self.item(i['id'])['deleted'])
  self.remove(self.item(i['id']));i=self.item(i['id']);data={'id':i['id'],'version':i['version']}
  with d.transaction() as c:d.command(c,self.dad,self.fid,'item.restore','restore-key',data)
  self.remove(self.item(i['id']))
  with d.transaction() as c:d.command(c,self.dad,self.fid,'item.restore','restore-key',data)
  self.assertTrue(self.item(i['id'])['deleted'])

 def test_concurrent_edits_accept_only_one_resource_version(self):
  i=self.item();results=[]
  def edit(quantity):
   try:self.update(i,quantity=quantity);results.append('saved')
   except d.Problem as e:results.append(e.status)
  threads=[threading.Thread(target=edit,args=(q,)) for q in (2,3)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertCountEqual(results,['saved',409])

 def test_restore_to_new_executor_reopens_completed_item(self):
  i=self.item();self.update(i,status='done');self.remove(self.item(i['id']))
  self.restore(self.item(i['id']),assignee=self.mom)
  i=self.item(i['id']);self.assertEqual(i['status'],'todo');self.assertIsNone(i['completed_at'])
