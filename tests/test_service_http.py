import unittest
import test_http


class ServiceHttpTests(unittest.TestCase):
 setUpClass=classmethod(test_http.HttpTests.setUpClass.__func__)
 tearDownClass=classmethod(test_http.HttpTests.tearDownClass.__func__)
 req=test_http.HttpTests.req
 account=test_http.HttpTests.account
 def test_support_is_private_idempotent_and_csrf_protected(self):
  user=self.account('http-service-user');other=self.account('http-service-other');token,csrf=user['session_token'],user['csrf']
  data={'title':'合成使用反馈','body':'本人手动提交，不附家庭数据','category':'usage','confirmed':True,'request_id':'http-ticket'}
  self.assertEqual(self.req('/api/support',data,token)[0],403)
  status,first=self.req('/api/support',data,token,csrf);self.assertEqual(status,200,first)
  self.assertEqual(self.req('/api/support',data,token,csrf),(200,first));self.assertEqual(self.req('/api/support',dict(data,body='不同内容'),token,csrf)[0],409)
  _,listing=self.req('/api/support',token=token);self.assertEqual(len(listing['tickets']),1);row=listing['tickets'][0];self.assertNotIn('input_hash',row)
  _,peer=self.req('/api/support',token=other['session_token']);self.assertEqual(peer['tickets'],[])
  self.assertEqual(self.req('/api/support/withdraw',row,other['session_token'],other['csrf'])[0],403)
  self.assertEqual(self.req('/api/support/withdraw',row,token,csrf)[0],200)
  _,listing=self.req('/api/support',token=token);self.assertEqual(listing['tickets'][0]['body'],'')
 def test_reading_and_usage_require_current_family_scope_and_admin_endpoints_are_system_only(self):
  user=self.account('http-service-dad');other=self.account('http-service-outsider');token,csrf=user['session_token'],user['csrf']
  _,f=self.req('/api/families',{'name':'合成阅读家庭'},token,csrf);fid=f['family_id']
  status,r=self.req('/api/knowledge?family_id='+fid+'&id=base-0',token=token);self.assertEqual(status,200,r);self.assertIn('rights',r['knowledge'])
  self.assertEqual(self.req('/api/knowledge?family_id='+fid+'&id=base-0',token=other['session_token'])[0],403)
  _,usage=self.req('/api/ai/usage?family_id='+fid,token=token);self.assertEqual(usage['my_successful'],0)
  self.assertEqual(self.req('/api/ai/usage?family_id='+fid,token=other['session_token'])[0],403)
  for path in ('/api/admin/support','/api/admin/operations'):self.assertEqual(self.req(path,token=token)[0],403)
