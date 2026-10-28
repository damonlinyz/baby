from pathlib import Path
import shutil
import subprocess
import unittest


class ClientTests(unittest.TestCase):
 @unittest.skipUnless(shutil.which('node'), 'Node.js is required for client tests')
 def test_client_request_recovery_and_session_boundaries(self):
  root=Path(__file__).resolve().parents[1]
  files=sorted(str(path) for path in (root/'tests').glob('client_*.test.cjs'))
  for file in files:
   with self.subTest(file=Path(file).name):
    result=subprocess.run(['node',file],cwd=root,capture_output=True,text=True,timeout=45)
    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
