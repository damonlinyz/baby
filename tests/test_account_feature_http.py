import unittest
import test_http


class AccountFeatureHttpTests(unittest.TestCase):
 setUpClass=classmethod(test_http.HttpTests.setUpClass.__func__)
 tearDownClass=classmethod(test_http.HttpTests.tearDownClass.__func__)
 req=test_http.HttpTests.req
 account=test_http.HttpTests.account
 def test_change_password_invalidates_original_session_and_requires_new_password(self):
  a=self.account('change-http');token=a['session_token'];csrf=a['csrf']
  self.assertEqual(self.req('/api/account/password',{'password':'test-password-123','new_password':'changed-password-123'},token)[0],403)
  status,result=self.req('/api/account/password',{'password':'test-password-123','new_password':'changed-password-123'},token,csrf);self.assertEqual(status,200,result)
  self.assertEqual(self.req('/api/me',token=token)[0],401)
  self.assertEqual(self.req('/api/login',{'login':'change-http','password':'test-password-123'})[0],401)
  self.assertEqual(self.req('/api/login',{'login':'change-http','password':'changed-password-123'})[0],200)
 def test_recovery_code_is_once_and_reset_does_not_log_user_in(self):
  a=self.account('recover-http');token=a['session_token'];csrf=a['csrf']
  _,proof=self.req('/api/account/recovery-code',{'password':'test-password-123'},token,csrf)
  self.assertIn('code',proof)
  _,settings=self.req('/api/account/security',token=token);self.assertTrue(settings['recovery_ready']);self.assertNotIn('digest',settings)
  data={'login':'recover-http','code':proof['code'],'new_password':'recovered-password-123'}
  status,r=self.req('/api/account/recover',data);self.assertEqual(status,200,r);self.assertNotIn('session_token',r)
  self.assertEqual(self.req('/api/me',token=token)[0],401);self.assertEqual(self.req('/api/account/recover',data)[0],400)
 def test_library_is_authenticated_and_wechat_code_adapter_is_disabled(self):
  a=self.account('library-http');self.assertEqual(self.req('/api/task-library')[0],401)
  _,r=self.req('/api/task-library',token=a['session_token']);self.assertEqual(len(r['templates']),84)
  self.assertEqual(self.req('/api/poster/code',token=a['session_token'])[0],503)


class RecoveryRateHttpTests(unittest.TestCase):
 setUpClass=classmethod(test_http.HttpTests.setUpClass.__func__)
 tearDownClass=classmethod(test_http.HttpTests.tearDownClass.__func__)
 req=test_http.HttpTests.req
 def test_invalid_codes_consume_persisted_rate_budget(self):
  for _ in range(6):self.assertEqual(self.req('/api/account/recover',{'login':'unknown','code':'invalid','new_password':'new-password-123'})[0],400)
  self.assertEqual(self.req('/api/account/recover',{'login':'unknown','code':'invalid','new_password':'new-password-123'})[0],429)
