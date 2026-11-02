"""Isolated static-only acceptance at a GitHub Pages-style project subpath."""
import functools
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import download_site


def main():
    with tempfile.TemporaryDirectory(prefix='baba-public-page-') as directory:
        download_site.build(ROOT,Path(directory)/'baby')
        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=directory))
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        env=dict(os.environ,TEST_BASE='http://127.0.0.1:'+str(server.server_address[1])+'/baby/')
        try:subprocess.run(['node',str(ROOT/'tests/browser/download_page.cjs')],cwd=ROOT,env=env,check=True,timeout=90)
        finally:server.shutdown();server.server_close();worker.join(timeout=5)


if __name__=='__main__':main()
