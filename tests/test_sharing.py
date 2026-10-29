import json
import unittest
from unittest.mock import patch

import test_domain
import domain as d
import sharing
import maintenance


class SharingTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap
 def create(self,**changes):return self.call(self.dad,'share.create',dict({'item_ids':[self.snap(self.dad)['items'][0]['id']],'confirmed':True,'hours':24},**changes))
 def view(self,token):
  with d.transaction() as c:return sharing.view(c,token)

 def test_public_template_is_generic_and_private_snapshot_is_field_minimized(self):
  public=sharing.public_template();self.assertEqual(len(public['items']),9);self.assertNotIn(self.fid,json.dumps(public))
  s=self.create();r=self.view(s['token']);self.assertEqual(set(r['items'][0]),{'name','pack','quantity','level','status'})
  for value in (self.fid,self.dad,'测试家庭','爸爸'):self.assertNotIn(value,json.dumps(r,ensure_ascii=False))
  self.assertNotIn('token',self.snap(self.dad)['shares'][0]);self.assertNotIn(s['token'],s['id'])

 def test_invalid_expired_revoked_or_changed_links_are_uniformly_unavailable(self):
  for token in ('bad','x'*45):
   with self.assertRaises(d.Problem) as e:self.view(token)
   self.assertEqual(e.exception.status,410)
  s=self.create()
  with patch.object(d,'now',return_value=s['expires_at']):
   with self.assertRaises(d.Problem):self.view(s['token'])
   with d.transaction() as c:sharing.expire(c,self.fid)
  with self.assertRaises(d.Problem):self.view(s['token'])
  self.call(self.dad,'share.revoke',{'id':s['id']});self.call(self.dad,'share.revoke',{'id':s['id']})
  with self.assertRaises(d.Problem):self.view(s['token'])
  with d.transaction() as c:self.assertEqual(d.get(c,'share',s['id'])['items'],[])

 def test_content_mutation_invalidates_instead_of_exposing_future_changes(self):
  item=self.snap(self.dad)['items'][0];s=self.create();self.call(self.dad,'item.update',{'id':item['id'],'version':item['version'],'name':'不应自动分享的新私密名称'})
  with self.assertRaises(d.Problem):self.view(s['token'])

 def test_leave_and_rejoin_do_not_revive_a_previous_link(self):
  s=self.create();self.call(self.mom,'member.leave',{});invite=self.call(self.dad,'invite.create',{'role':'mom'})
  with d.transaction() as c:d.join(c,self.mom,invite['invite_code'])
  with self.assertRaises(d.Problem):self.view(s['token'])

 def test_create_requires_coordinator_selection_and_explicit_confirmation(self):
  item=self.snap(self.dad)['items'][0]
  for user in (self.mom,self.other):
   with self.assertRaises(d.Problem):self.call(user,'share.create',{'item_ids':[item['id']],'confirmed':True})
  for changes in ({'confirmed':False},{'item_ids':[]},{'item_ids':[item['id'],item['id']]},{'hours':True},{'hours':169},{'item_ids':['not-an-item']}):
   with self.assertRaises(d.Problem):self.create(**changes)
  with d.transaction() as c:self.assertEqual(d.rows(c,'share',self.fid),[])

 def test_other_family_ids_cannot_select_or_revoke_and_command_replay_is_stable(self):
  item=self.snap(self.dad)['items'][0]
  with d.transaction() as c:other=d.create_family(c,self.dad,{'name':'别的家庭'})['family_id']
  with self.assertRaises(d.Problem):
   with d.transaction() as c:d.mutate(c,self.dad,other,'share.create',{'item_ids':[item['id']],'confirmed':True})
  data={'item_ids':[item['id']],'confirmed':True}
  with d.transaction() as c:first=d.command(c,self.dad,self.fid,'share.create','share-key',data)
  with d.transaction() as c:self.assertEqual(d.command(c,self.dad,self.fid,'share.create','share-key',data),first)
  with self.assertRaises(d.Problem):
   with d.transaction() as c:d.mutate(c,self.dad,other,'share.revoke',{'id':first['id']})

 def test_account_deletion_and_restoration_never_resurrect_bearer_links(self):
  s=self.create();snapshot=d.DB.parent/'shared.sqlite3';maintenance.backup(d.DB,snapshot)
  self.call(self.dad,'share.revoke',{'id':s['id']});restored=d.DB.parent/'restored.sqlite3';maintenance.restore(snapshot,d.deletion_ledger_path(),restored)
  with patch.object(d,'DB',restored):
   with self.assertRaises(d.Problem):self.view(s['token'])
  s=self.create()
  with d.transaction() as c:d.delete_account(c,self.mom,'long-password-123')
  with self.assertRaises(d.Problem):self.view(s['token'])
