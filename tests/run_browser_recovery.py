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
 if scenario not in ('recovery','recurring','bag','calendar','reports'):raise ValueError('unknown browser scenario')
 root=Path(__file__).resolve().parents[1]
 with tempfile.TemporaryDirectory(prefix='baba-browser-') as directory:
  with socket.socket() as listener:
   listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
  base='http://127.0.0.1:'+str(port)
  today=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).date()
  env=dict(os.environ,BABA_DB=str(Path(directory)/'synthetic.sqlite3'),HOST='127.0.0.1',PORT=str(port),
           PUBLIC_ORIGIN=base,TEST_BASE=base,TEST_EDD=str(today+datetime.timedelta(days=100)),
           PAYMENTS_ENABLED='0',AI_SALES_APPROVED='0',AI_API_KEY='',PYTHONDONTWRITEBYTECODE='1')
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
