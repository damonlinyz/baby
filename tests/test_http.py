import unittest,tempfile,subprocess,os,json,urllib.request,urllib.error,socket,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class HttpTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();s=socket.socket();s.bind(('127.0.0.1',0));cls.port=s.getsockname()[1];s.close();cls.base='http://127.0.0.1:'+str(cls.port)
  env=dict(os.environ,PORT=str(cls.port),PUBLIC_ORIGIN=cls.base,BABA_DB=str(Path(cls.tmp.name)/'http.sqlite3'),PYTHONDONTWRITEBYTECODE='1');env.pop('ADMIN_USER_ID',None);env.pop('PAYMENTS_ENABLED',None)
  cls.proc=subprocess.Popen([sys.executable,str(ROOT/'backend/server.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  for _ in range(100):
   try:urllib.request.urlopen(cls.base+'/api/health',timeout=.2);break
   except Exception:time.sleep(.05)
  else:raise RuntimeError('test server did not start')
 @classmethod
 def tearDownClass(cls):cls.proc.terminate();cls.proc.wait(timeout=5);cls.tmp.cleanup()
 def req(self,path,data=None,token='',csrf='',origin=None):
  headers={'Content-Type':'application/json'}
  if token:headers['Authorization']='Bearer '+token
  if csrf:headers['X-CSRF-Token']=csrf
  if origin:headers['Origin']=origin
  req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
  try:
   with urllib.request.urlopen(req) as r:return r.status,json.load(r)
  except urllib.error.HTTPError as e:
   try:return e.code,json.load(e)
   except:return e.code,{}
 def account(self,login):
  status,r=self.req('/api/register',{'login':login,'password':'test-password-123','name':'测试家人'});self.assertEqual(status,200);return r
 def test_session_csrf_and_persistence(self):
  a=self.account('http-dad');token=a['session_token'];csrf=a['csrf']
  self.assertEqual(self.req('/api/families',{'name':'家'},token)[0],403)
  status,r=self.req('/api/families',{'name':'家'},token,csrf);self.assertEqual(status,200);fid=r['family_id']
  status,s=self.req('/api/state?family_id='+fid,token=token);self.assertEqual(status,200);self.assertEqual(len(s['tasks']),4)
  t=s['tasks'][0];command={'family_id':fid,'action':'task.update','request_id':'http-done','data':{'id':t['id'],'version':t['version'],'status':'done'}}
  self.assertEqual(self.req('/api/action',command,token,csrf)[0],200);self.assertEqual(self.req('/api/action',command,token,csrf)[0],200)
  _,s=self.req('/api/state?family_id='+fid,token=token);self.assertEqual(next(x for x in s['tasks'] if x['id']==t['id'])['status'],'done')
  self.assertEqual(self.req('/api/families',{'name':'恶意跨域'},token,csrf,'https://evil.invalid')[0],403)
 def test_static_source_and_private_database_not_exposed(self):
  self.assertEqual(self.req('/backend/server.py')[0],404);self.assertEqual(self.req('/private/baba.sqlite3')[0],404)
 def test_no_fake_payment(self):
  a=self.account('http-pay');_,f=self.req('/api/families',{'name':'支付测试'},a['session_token'],a['csrf'])
  self.assertEqual(self.req('/api/orders',{'family_id':f['family_id'],'provider':'wechat','platform':'weapp','sku':'core','request_id':'test-pay'},a['session_token'],a['csrf'])[0],503)
 def test_unauthenticated_state(self):self.assertEqual(self.req('/api/state?family_id=guess')[0],401)
 def test_admin_is_not_family_coordinator(self):
  a=self.account('http-admin-test');self.assertEqual(self.req('/api/admin',token=a['session_token'])[0],403)
