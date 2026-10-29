"""Optional Playwright acceptance test; always creates a temporary local service."""
import datetime
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo


def main(scenario='recovery'):
 if scenario not in ('recovery','recurring','bag','calendar','reports','recurring_rules','task_details','care_features','community_accounts','service_features'):raise ValueError('unknown browser scenario')
 root=Path(__file__).resolve().parents[1]
 with tempfile.TemporaryDirectory(prefix='baba-browser-') as directory:
  with socket.socket() as listener:
   listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
  base='http://127.0.0.1:'+str(port)
  today=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).date()
  env=dict(os.environ,BABA_DB=str(Path(directory)/'synthetic.sqlite3'),HOST='127.0.0.1',PORT=str(port),
           PUBLIC_ORIGIN=base,TEST_BASE=base,TEST_EDD=str(today+datetime.timedelta(days=100)),
           PAYMENTS_ENABLED='0',WX_NOTIFY_ENABLED='0',WX_POSTER_ENABLED='0',AI_SALES_APPROVED='0',AI_API_KEY='',PYTHONDONTWRITEBYTECODE='1')
  if scenario=='service_features':
   sys.path.insert(0,str(root/'backend'))
   import domain as d
   d.DB=Path(env['BABA_DB']);d.init()
   with d.transaction() as c:
    admin=d.auth_register(c,{'login':'service-admin','name':'合成系统管理员','password':'browser-password-123'})
    queue_family=d.create_family(c,admin,{'name':'合成队列家庭'})['family_id']
    recipient=d.auth_register(c,{'login':'queue-recipient','name':'合成接收人','password':'browser-password-123'})
    invitation=d.mutate(c,admin,queue_family,'invite.create',{'role':'mom'});d.join(c,recipient,invitation['invite_code']);d.mutate(c,recipient,queue_family,'pregnancy.create',{'consent':True})
    pregnancy=d.rows(c,'pregnancy',queue_family)[0]
    d.put(c,'outbox',{'id':'browser-failed-outbox','pregnancy_id':pregnancy['id'],'pregnancy_revision':pregnancy['revision'],'recipients':[recipient],'event_id':'browser-synthetic-event','status':'failed','attempts':3,'available_at':0,'created_at':d.now(),'private':'合成的私密日期标记'},queue_family)

   env['ADMIN_USER_ID']=admin
  with (Path(directory)/'server.log').open('w') as log:
   server=subprocess.Popen([sys.executable,str(root/'backend/server.py')],cwd=root,env=env,stdout=log,stderr=log)
   try:
    # Local health checks must not go through an inherited outbound HTTP proxy.
    local=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
     if server.poll() is not None:raise RuntimeError('temporary server exited before becoming ready')
     try:
      with local.open(base+'/api/health',timeout=.2) as response:
       if response.status==200:break
     except (OSError,urllib.error.URLError):pass
     time.sleep(.05)
    else:raise RuntimeError('temporary server did not become ready')
    subprocess.run(['node',str(root/'tests/browser'/(scenario+'.cjs'))],cwd=root,env=env,check=True,timeout=120)
   finally:
    server.terminate()
    try:server.wait(timeout=5)
    except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)


if __name__=='__main__':main()
