import unittest,tempfile,subprocess,os,json,urllib.request,urllib.error,socket,time,sys,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class HttpTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();s=socket.socket();s.bind(('127.0.0.1',0));cls.port=s.getsockname()[1];s.close();cls.base='http://127.0.0.1:'+str(cls.port)
  env=dict(os.environ,PORT=str(cls.port),PUBLIC_ORIGIN=cls.base,BABA_DB=str(Path(cls.tmp.name)/'http.sqlite3'),PYTHONDONTWRITEBYTECODE='1',WX_NOTIFY_ENABLED='0',WX_POSTER_ENABLED='0');env.pop('ADMIN_USER_ID',None);env.pop('PAYMENTS_ENABLED',None)
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
 def test_action_retry_rejects_changed_content(self):
  a=self.account('http-retry');token=a['session_token'];csrf=a['csrf']
  _,f=self.req('/api/families',{'name':'重试测试'},token,csrf);fid=f['family_id']
  command={'family_id':fid,'action':'task.create','request_id':'bound-request','data':{'title':'只创建一次'}}
  status,first=self.req('/api/action',command,token,csrf);self.assertEqual(status,200)
  self.assertEqual(self.req('/api/action',command,token,csrf),(200,first))
  command['data']={'title':'不同内容'}
  self.assertEqual(self.req('/api/action',command,token,csrf)[0],409)
  command['request_id']='invalid-data';command['data']=[]
  self.assertEqual(self.req('/api/action',command,token,csrf)[0],400)
  _,s=self.req('/api/state?family_id='+fid,token=token)
  self.assertEqual(len(s['tasks']),5)

 def test_revocation_export_and_subject_deletion(self):
  dad=self.account('http-cleanup-dad');mom=self.account('http-cleanup-mom')
  dt,dc=dad['session_token'],dad['csrf'];mt,mc=mom['session_token'],mom['csrf']
  _,f=self.req('/api/families',{'name':'删除测试'},dt,dc);fid=f['family_id'];seq=0
  def act(token,csrf,action,data):
   nonlocal seq
   seq+=1
   status,r=self.req('/api/action',{'family_id':fid,'action':action,'data':data,'request_id':'cleanup-'+str(seq)},token,csrf)
   self.assertEqual(status,200,r);return r
  inv=act(dt,dc,'invite.create',{'role':'mom'})
  self.assertEqual(self.req('/api/join',{'code':inv['invite_code']},mt,mc)[0],200)
  act(mt,mc,'pregnancy.create',{'consent':True})
  _,initial=self.req('/api/state?family_id='+fid,token=mt);p=initial['pregnancy']
  change={'id':p['id'],'version':p['version'],'event':'set_edd','date':str(datetime.date.today()+datetime.timedelta(days=100))}
  status,preview=self.req('/api/pregnancy/preview',{'family_id':fid,'data':change},mt,mc)
  self.assertEqual(status,200);self.assertGreater(preview['counts']['created'],0)
  _,unchanged=self.req('/api/state?family_id='+fid,token=mt)
  self.assertEqual(unchanged['pregnancy']['state'],'dating')
  act(mt,mc,'pregnancy.event',dict(change,preview_token=preview['preview_token']))
  _,changed=self.req('/api/state?family_id='+fid,token=mt)
  self.assertEqual(changed['pregnancy']['state'],'pregnant');self.assertIn('timeline',changed['pregnancy'])
  for scope in ('health_read','health_edit'):act(mt,mc,'consent.update',{'scope':scope,'grantee':dad['user']['id'],'enabled':True})
  act(dt,dc,'appointment.create',{'title':'合成预约','date':'2026-10-22'})
  _,before=self.req('/api/export?family_id='+fid,token=dt)
  self.assertEqual(len(before['appointments']),1)
  act(mt,mc,'consent.update',{'scope':'health_read','grantee':dad['user']['id'],'enabled':False})
  _,after=self.req('/api/export?family_id='+fid,token=dt)
  self.assertEqual(after['appointments'],[]);self.assertEqual(after['pregnancies'],[])
  self.assertEqual(self.req('/api/account/delete',{'password':'wrong'},mt,mc)[0],403)
  self.assertEqual(self.req('/api/account/delete',{'password':'test-password-123'},mt,mc)[0],200)
  self.assertEqual(self.req('/api/me',token=mt)[0],401)
  status,s=self.req('/api/state?family_id='+fid,token=dt)
  self.assertEqual(status,200);self.assertIsNone(s['pregnancy']);self.assertEqual(len(s['members']),1)

 def test_child_preview_sharing_export_and_timezone(self):
  dad=self.account('http-child-dad');mom=self.account('http-child-mom')
  dt,dc=dad['session_token'],dad['csrf'];mt,mc=mom['session_token'],mom['csrf']
  _,f=self.req('/api/families',{'name':'多胎测试'},dt,dc);fid=f['family_id'];seq=0
  def act(token,csrf,action,data):
   nonlocal seq
   seq+=1
   status,r=self.req('/api/action',{'family_id':fid,'action':action,'data':data,'request_id':'baby-http-'+str(seq)},token,csrf)
   self.assertEqual(status,200,r);return r
  def state(token):return self.req('/api/state?family_id='+fid,token=token)[1]
  invite=act(dt,dc,'invite.create',{'role':'mom'});self.assertEqual(self.req('/api/join',{'code':invite['invite_code']},mt,mc)[0],200)
  act(mt,mc,'pregnancy.create',{'consent':True});p=state(mt)['pregnancy'];today=p['timeline']['as_of']
  act(mt,mc,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'set_edd','date':str(datetime.date.fromisoformat(today)+datetime.timedelta(days=100))})
  p=state(mt)['pregnancy'];act(mt,mc,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'birth','date':today,'babies':2})
  act(mt,mc,'consent.update',{'scope':'health_read','grantee':dad['user']['id'],'enabled':True})
  self.assertEqual(state(dt)['babies'],[])
  baby=state(mt)['babies'][0];data={'id':baby['id'],'version':baby['version'],'status':'lost','date':today,'guardian_consent':True,'reason':'child-private-note'}
  status,preview=self.req('/api/babies/preview',{'family_id':fid,'data':data},mt,mc);self.assertEqual(status,200)
  self.assertEqual(state(mt)['babies'][0]['status'],'unconfirmed')
  act(mt,mc,'baby.update',dict(data,preview_token=preview['preview_token']))
  self.assertEqual(state(mt)['pregnancy']['state'],'postpartum')
  act(mt,mc,'consent.update',{'scope':'baby_read','grantee':dad['user']['id'],'enabled':True,'guardian_consent':True})
  _,export=self.req('/api/export?family_id='+fid,token=dt);self.assertEqual(len(export['babies']),2);self.assertEqual(len(export['baby_events']),1)
  act(mt,mc,'consent.update',{'scope':'baby_read','grantee':dad['user']['id'],'enabled':False})
  _,export=self.req('/api/export?family_id='+fid,token=dt);self.assertEqual(export['babies'],[]);self.assertEqual(export['baby_events'],[]);self.assertNotIn('child-private-note',json.dumps(export))
  p=state(mt)['pregnancy'];status,_=self.req('/api/pregnancy/preview',{'family_id':fid,'data':{'id':p['id'],'version':p['version'],'event':'correct_birth_record','birth_event_id':p['birth_event_id'],'confirm_correction':True,'reason':'合成误录'}},mt,mc);self.assertEqual(status,409)
  f=state(dt)['family'];act(dt,dc,'family.update',{'version':f['version'],'timezone':'Asia/Tokyo'})
  self.assertEqual(state(mt)['pregnancy']['timeline']['timezone'],'Asia/Tokyo')

 def test_calendar_route_requires_current_membership_and_never_caches_export(self):
  dad=self.account('http-calendar-dad');other=self.account('http-calendar-outsider');token=dad['session_token'];csrf=dad['csrf']
  _,family=self.req('/api/families',{'name':'日历验证'},token,csrf);fid=family['family_id'];path='/api/calendar?family_id='+fid
  self.assertEqual(self.req(path)[0],401);self.assertEqual(self.req(path,token=other['session_token'])[0],403)
  self.req('/api/action',{'family_id':fid,'action':'task.create','request_id':'calendar-task','data':{'title':'HTTP 日历事项','due_date':str(datetime.date.today())}},token,csrf)
  with urllib.request.urlopen(urllib.request.Request(self.base+path+'&start='+str(datetime.date.today())+'&end='+str(datetime.date.today()),headers={'Authorization':'Bearer '+token})) as response:
   self.assertEqual(response.headers['Cache-Control'],'no-store');result=json.load(response)
  self.assertEqual(result['event_count'],1);self.assertTrue(result['filename'].endswith('.ics'));self.assertIn('HTTP 日历事项',result['content'])
  self.assertEqual(self.req(path+'&start=bad',token=token)[0],400)
  self.req('/api/logout',{},token,csrf);self.assertEqual(self.req(path,token=token)[0],401)

 def test_weekly_report_requires_membership_recomputes_facts_and_is_not_cached(self):
  dad=self.account('http-weekly-dad');other=self.account('http-weekly-outsider');token=dad['session_token'];csrf=dad['csrf']
  _,family=self.req('/api/families',{'name':'每周验证'},token,csrf);fid=family['family_id'];path='/api/report?family_id='+fid
  self.assertEqual(self.req(path)[0],401);self.assertEqual(self.req(path,token=other['session_token'])[0],403)
  _,state=self.req('/api/state?family_id='+fid,token=token);task=state['tasks'][0]
  status,_=self.req('/api/action',{'family_id':fid,'action':'task.update','request_id':'weekly-done','data':{'id':task['id'],'version':task['version'],'status':'done'}},token,csrf);self.assertEqual(status,200)
  with urllib.request.urlopen(urllib.request.Request(self.base+path,headers={'Authorization':'Bearer '+token})) as response:
   self.assertEqual(response.headers['Cache-Control'],'no-store');report=json.load(response)
  self.assertEqual(report['counts'],{'tasks':1,'items':0});self.assertTrue(report['is_current'])
  _,historical=self.req(path+'&week='+report['previous_week'],token=token);self.assertEqual(historical['counts']['tasks'],0);self.assertEqual(historical['next_week'],report['week_start'])
  _,state=self.req('/api/state?family_id='+fid,token=token);self.assertEqual(state['weekly_report'],report)
  self.assertEqual(self.req(path+'&week=bad',token=token)[0],400);self.assertEqual(self.req(path+'&week=9999-12-31',token=token)[0],400)
  self.req('/api/logout',{},token,csrf);self.assertEqual(self.req(path,token=token)[0],401)

 def test_task_reference_route_rechecks_assignment_and_current_links(self):
  dad=self.account('http-detail-dad');member=self.account('http-detail-member');token,csrf=dad['session_token'],dad['csrf'];mt,mc=member['session_token'],member['csrf']
  _,family=self.req('/api/families',{'name':'任务知识验证'},token,csrf);fid=family['family_id']
  def action(name,data,key):
   status,result=self.req('/api/action',{'family_id':fid,'action':name,'data':data,'request_id':key},token,csrf);self.assertEqual(status,200,result);return result
  invitation=action('invite.create',{'role':'member'},'detail-invite');self.assertEqual(self.req('/api/join',{'code':invitation['invite_code']},mt,mc)[0],200)
  t=action('task.create',{'title':'任务引用','steps':['盘点'],'knowledge_ids':['base-1'],'assignee':member['user']['id']},'detail-task')
  path='/api/task/knowledge?family_id='+fid+'&task_id='+t['id']+'&knowledge_id=base-1'
  self.assertEqual(self.req(path)[0],401);self.assertEqual(self.req(path,token=mt)[0],200)
  _,state=self.req('/api/state?family_id='+fid,token=token);task=next(task for task in state['tasks'] if task['id']==t['id'])
  action('task.update',{'id':task['id'],'version':task['version'],'assignee':dad['user']['id']},'detail-reassign')
  self.assertEqual(self.req(path,token=mt)[0],403);self.assertEqual(self.req(path+'&unused=value',token=token)[0],200)
  self.assertEqual(self.req(path.replace('knowledge_id=base-1','knowledge_id=base-0'),token=token)[0],409)
