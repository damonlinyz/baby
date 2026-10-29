import json
import os
import unittest
from unittest.mock import patch
import test_domain
import domain as d
import posters
import push_notifications


class PosterTests(unittest.TestCase):
 def test_disabled_platform_code_does_not_call_wechat(self):
  with patch.dict(os.environ,{'WX_POSTER_ENABLED':'0'}),patch.object(posters.urllib.request,'urlopen') as remote:
   with self.assertRaises(d.Problem) as e:posters.mini_code()
   self.assertEqual(e.exception.status,503);remote.assert_not_called()
 def test_platform_code_contains_only_fixed_public_scene_and_release_page(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return b'\x89PNG\r\n\x1a\nsynthetic-code'
  with patch.dict(os.environ,{'WX_POSTER_ENABLED':'1','WX_APP_ID':'synthetic','WX_APP_SECRET':'synthetic'}),patch.object(push_notifications,'get_token',return_value='test-token'),patch.object(posters.urllib.request,'urlopen',return_value=Response()) as remote:
   image=posters.mini_code();self.assertTrue(image.startswith(b'\x89PNG'))
   payload=json.loads(remote.call_args[0][0].data);self.assertEqual(payload,{'scene':'template','page':'pages/main/main','check_path':True,'env_version':'release','width':280})
 def test_platform_rejection_never_returns_fake_code(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return b'{"errcode":40001}'
  with patch.dict(os.environ,{'WX_POSTER_ENABLED':'1','WX_APP_ID':'synthetic','WX_APP_SECRET':'synthetic'}),patch.object(push_notifications,'get_token',return_value='test-token'),patch.object(posters.urllib.request,'urlopen',return_value=Response()):
   with self.assertRaises(d.Problem) as e:posters.mini_code()
   self.assertNotIn('40001',e.exception.message)
