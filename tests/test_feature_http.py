import unittest
import urllib.request
import json

import test_http


class FeatureHttpTests(unittest.TestCase):
 setUpClass=classmethod(test_http.HttpTests.setUpClass.__func__)
 tearDownClass=classmethod(test_http.HttpTests.tearDownClass.__func__)
 req=test_http.HttpTests.req
 account=test_http.HttpTests.account
 def family(self,prefix):
  a=self.account(prefix);_,f=self.req('/api/families',{'name':'功能验收家庭'},a['session_token'],a['csrf']);return a,f['family_id']
 def action(self,a,fid,name,data,key):
  status,r=self.req('/api/action',{'family_id':fid,'action':name,'data':data,'request_id':key},a['session_token'],a['csrf']);self.assertEqual(status,200,r);return r

 def test_public_template_and_bearer_view_work_without_login_but_mutations_do_not(self):
  status,public=self.req('/api/public/template');self.assertEqual(status,200);self.assertEqual(len(public['items']),9)
  a,fid=self.family('http-feature-share');_,s=self.req('/api/state?family_id='+fid,token=a['session_token']);item=s['items'][0]
  share=self.action(a,fid,'share.create',{'item_ids':[item['id']],'confirmed':True},'http-share')
  status,r=self.req('/api/shared',{'token':share['token']});self.assertEqual(status,200);self.assertNotIn('family',r);self.assertNotIn('note',r['items'][0])
  self.assertEqual(self.req('/api/shared',{'token':'bad'})[0],410)
  self.assertEqual(self.req('/api/shared',{'token':share['token']},origin='https://untrusted.invalid')[0],403)
  self.action(a,fid,'share.revoke',{'id':share['id']},'http-share-revoke');self.assertEqual(self.req('/api/shared',{'token':share['token']})[0],410)

 def test_automatic_briefing_is_recipient_scoped_and_not_cached(self):
  a,fid=self.family('http-feature-brief');other=self.account('http-feature-other')
  path='/api/briefings?family_id='+fid;self.assertEqual(self.req(path)[0],401);self.assertEqual(self.req(path,token=other['session_token'])[0],403)
  result=self.action(a,fid,'briefing.refresh',{},'http-brief');id=result['id']
  with urllib.request.urlopen(urllib.request.Request(self.base+'/api/briefing?family_id='+fid+'&id='+id,headers={'Authorization':'Bearer '+a['session_token']})) as response:
   self.assertEqual(response.headers['Cache-Control'],'no-store');self.assertEqual(json.load(response)['status'],'active')
  self.assertEqual(self.req('/api/briefing?family_id='+fid+'&id='+id,token=other['session_token'])[0],403)

 def test_completed_tasks_unlock_only_the_current_users_badges_and_are_correctable(self):
  a,fid=self.family('http-feature-badge');_,s=self.req('/api/state?family_id='+fid,token=a['session_token']);t=s['tasks'][0]
  self.action(a,fid,'task.update',{'id':t['id'],'version':t['version'],'status':'done'},'http-badge-done')
  _,s=self.req('/api/state?family_id='+fid,token=a['session_token']);self.assertTrue(next(b for b in s['engagement']['badges'] if b['id']=='first-action')['earned'])
  t=next(task for task in s['tasks'] if task['id']==t['id']);self.action(a,fid,'task.update',{'id':t['id'],'version':t['version'],'status':'todo'},'http-badge-undo')
  _,s=self.req('/api/state?family_id='+fid,token=a['session_token']);self.assertFalse(next(b for b in s['engagement']['badges'] if b['id']=='first-action')['earned'])
  self.action(a,fid,'engagement.checkin',{'confirmed':True},'http-checkin');_,s=self.req('/api/state?family_id='+fid,token=a['session_token']);self.assertTrue(s['engagement']['checked_today'])

 def test_reminder_preferences_remain_typed_and_private(self):
  a,fid=self.family('http-feature-prefs');self.action(a,fid,'notification.preferences',{'daily_limit':3,'merge_enabled':True,'weekly_enabled':True},'http-pref-save')
  _,s=self.req('/api/state?family_id='+fid,token=a['session_token']);self.assertEqual(s['reminder_preferences']['daily_limit'],3)
  status,_=self.req('/api/action',{'family_id':fid,'action':'notification.preferences','data':{'daily_limit':True},'request_id':'http-pref-invalid'},a['session_token'],a['csrf']);self.assertEqual(status,400)


class ShareRateHttpTests(unittest.TestCase):
 setUpClass=classmethod(test_http.HttpTests.setUpClass.__func__)
 tearDownClass=classmethod(test_http.HttpTests.tearDownClass.__func__)
 req=test_http.HttpTests.req
 def test_invalid_share_tokens_consume_rate_budget_too(self):
  for _ in range(120):self.assertEqual(self.req('/api/shared',{'token':'invalid'})[0],410)
  self.assertEqual(self.req('/api/shared',{'token':'invalid'})[0],429)
