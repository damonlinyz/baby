import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import test_backup_jobs
import domain as d
import backup_jobs as jobs
import offsite
import maintenance as m


class OffsiteTests(unittest.TestCase):
 setUpClass=classmethod(test_backup_jobs.BackupJobTests.setUpClass.__func__)
 setUp=test_backup_jobs.BackupJobTests.setUp
 tearDown=test_backup_jobs.BackupJobTests.tearDown
 root=test_backup_jobs.BackupJobTests.root
 run_job=test_backup_jobs.BackupJobTests.run_job
 def fake_remote(self):
  self.objects={};self.requests=[]
  def transport(method,url,data=None,**kw):
   self.requests.append((method,url,data))
   if method=='PUT':self.objects[url]=data;return b''
   if url not in self.objects:raise m.MaintenanceError('synthetic missing object')
   return b'' if method=='HEAD' else self.objects[url]
  return transport
 def test_sync_only_uploads_ciphertext_and_publishes_manifest_after_readback(self):
  self.run_job();transport=self.fake_remote()
  with patch.dict(os.environ,{'BABA_OFFSITE_URL':'https://storage.invalid/backup'}),patch.object(offsite,'remote',side_effect=transport):
   result=offsite.sync(d.DB,self.root(),'full');self.assertTrue(result['verified']);self.assertTrue(offsite.check(d.DB,self.root(),'full')['ok'])
  self.assertEqual([r[0] for r in self.requests[:4]],['PUT','GET','PUT','GET'])
  self.assertTrue(self.requests[0][1].endswith('/payload.cms'));self.assertTrue(self.requests[2][1].endswith('/manifest.json'))
  self.assertNotIn(b'long-password',self.requests[0][2]);self.assertNotIn(b'SQLite format',self.requests[0][2])
 def test_failed_remote_readback_cannot_commit_marker_or_success(self):
  self.run_job();transport=self.fake_remote()
  def corrupt(method,url,data=None,**kw):
   result=transport(method,url,data,**kw)
   return b'corrupt' if method=='GET' and url.endswith('/payload.cms') else result
  with patch.dict(os.environ,{'BABA_OFFSITE_URL':'https://storage.invalid/backup'}),patch.object(offsite,'remote',side_effect=corrupt):
   with self.assertRaises(m.MaintenanceError):offsite.sync(d.DB,self.root(),'full')
  self.assertFalse(any(url.endswith('manifest.json') for url in self.objects))
  self.assertEqual(jobs.read_json(self.root()/'full.offsite.json')['state'],'failed')
 def test_destination_change_remote_deletion_and_freshness_fail_health(self):
  self.run_job();transport=self.fake_remote()
  with patch.dict(os.environ,{'BABA_OFFSITE_URL':'https://storage.invalid/backup'}),patch.object(offsite,'remote',side_effect=transport):
   offsite.sync(d.DB,self.root(),'full')
   with patch.dict(os.environ,{'BABA_OFFSITE_URL':'https://new-storage.invalid/backup'}):
    with self.assertRaises(m.MaintenanceError):offsite.check(d.DB,self.root(),'full')
   self.objects.pop(next(url for url in self.objects if url.endswith('payload.cms')))
   with self.assertRaises(m.MaintenanceError):offsite.check(d.DB,self.root(),'full')
 def test_unencrypted_input_and_redirects_are_rejected(self):
  for value in ('http://storage.invalid/path','https://name:secret@storage.invalid','https://storage.invalid?token=abc'):
   with patch.dict(os.environ,{'BABA_OFFSITE_URL':value}):
    with self.assertRaises(m.MaintenanceError):offsite.endpoint('BABA_OFFSITE_URL')
  with self.assertRaises(m.MaintenanceError):offsite.NoRedirect().redirect_request(None,None,None,None,None,None)


class ReleaseTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'repo';self.root.mkdir()
  self.git('init','-q');self.git('config','user.email','synthetic@example.invalid');self.git('config','user.name','Synthetic')
  (self.root/'clients/app').mkdir(parents=True)
  manifest={'version':{'name':'0.2.1','code':201},'plus':{'distribute':{'android':{'packagename':''}}}}
  (self.root/'clients/app/manifest.json').write_text(json.dumps(manifest));(self.root/'app.js').write_text('const value=1;')
  (self.root/'private').mkdir();(self.root/'private/data.txt').write_text('private record');self.git('add','.');self.git('commit','-qm','synthetic source')
  path=Path(__file__).resolve().parents[1]/'tools/release_bundle.py';spec=importlib.util.spec_from_file_location('release_bundle',path);self.release=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.release)
 def git(self,*args):return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True,text=True)
 def test_source_is_reproducible_commit_bound_and_excludes_private_runtime(self):
  outputs=[Path(self.tmp.name)/name for name in ('first','second')]
  for output in outputs:
   result=self.release.build(self.root,output);self.assertFalse(result['contains_verified_apk'])
   with zipfile.ZipFile(output/'baba-source.zip') as source:self.assertNotIn('private/data.txt',source.namelist());self.assertEqual(source.read('app.js'),b'const value=1;')
   for line in (output/'SHA256SUMS').read_text().splitlines():
    digest,name=line.split('  ');self.assertEqual(digest,hashlib.sha256((output/name).read_bytes()).hexdigest())
  self.assertEqual((outputs[0]/'baba-source.zip').read_bytes(),(outputs[1]/'baba-source.zip').read_bytes())
 def test_dirty_source_and_unverified_apk_cannot_be_released(self):
  (self.root/'app.js').write_text('uncommitted');output=Path(self.tmp.name)/'out'
  with self.assertRaises(ValueError):self.release.build(self.root,output)
  self.git('checkout','--','app.js')
  with patch.object(self.release.shutil,'which',return_value=None):
   with self.assertRaises(ValueError):self.release.build(self.root,output,Path(self.tmp.name)/'fake.apk')
  self.assertFalse(output.exists())
