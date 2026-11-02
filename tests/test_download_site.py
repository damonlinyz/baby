import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import download_site as site
import publish_release
import release_bundle


def fixture():
    config={'repository':'damonlinyz/baby','android_asset':'baba-android.apk','android_package':'org.example.baba','android_signer_sha256':'b'*64,'minimum_android_version_code':202}
    apk={'name':'baba-android.apk','package':'org.example.baba','size_bytes':12345,'sha256':'a'*64,'signer_sha256':'b'*64,'min_sdk':23}
    manifest={'schema_version':2,'version':'0.2.2','version_code':202,'commit':'c'*40,'contains_verified_apk':True,'android_package':'org.example.baba','android':apk}
    base='https://github.com/damonlinyz/baby/releases/download/v0.2.2/'
    raw=site.canonical(manifest).encode();digest=hashlib.sha256(raw).hexdigest()
    sums='a'*64+'  baba-android.apk\n'+digest+'  release.json\n'+'d'*64+'  baba-source.zip\n'
    assets=[{'name':name,'state':'uploaded','size':12345 if name.endswith('.apk') else len(raw),'digest':'sha256:'+('a'*64 if name.endswith('.apk') else digest),'browser_download_url':base+name} for name in ('baba-android.apk','release.json','SHA256SUMS')]
    release={'draft':False,'prerelease':False,'tag_name':'v0.2.2','assets':assets,'published_at':'2026-11-02T01:00:00Z','html_url':'https://github.com/damonlinyz/baby/releases/tag/v0.2.2','body':'合成测试发布说明，不是真实安装包'}
    return config,release,manifest,sums


class DownloadSiteTests(unittest.TestCase):
    def test_only_public_assets_are_published_and_offline_defaults_to_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);shutil.copytree(ROOT/'website',root/'website');(root/'private').mkdir();(root/'private/health.json').write_text('must never be published')
            with patch.object(site,'latest') as remote:
                out=root/'public';site.build(root,out);remote.assert_not_called()
            self.assertEqual({p.name for p in out.iterdir()},set(site.FILES)|{'release.json','.nojekyll'})
            self.assertEqual(json.loads((out/'release.json').read_text())['status'],'pending')
            self.assertFalse((out/'backend').exists());self.assertFalse((out/'private').exists())
            with self.assertRaises(ValueError):site.build(root,out)
    def test_verified_stable_release_is_tag_pinned_and_preserves_only_public_metadata(self):
        config,release,manifest,sums=fixture();r=site.release_feed(config,release,manifest,sums,'c'*40)
        self.assertEqual(r['status'],'available');self.assertIn('/download/v0.2.2/',r['android']['url']);self.assertNotIn('/latest/',r['android']['url']);self.assertNotIn('private',r)
    def test_drafts_prereleases_missing_assets_and_unsafe_download_origins_are_rejected(self):
        config,release,manifest,sums=fixture()
        variants=[dict(release,draft=True),dict(release,prerelease=True),dict(release,tag_name='v0.2.2-beta'),dict(release,assets=[])]
        wrong=copy.deepcopy(release);wrong['assets'][0]['browser_download_url']='https://evil.invalid/baba-android.apk';variants.append(wrong)
        for changed in variants:
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):site.release_feed(config,changed,manifest,sums,'c'*40)
    def test_signature_commit_minimum_version_and_actual_github_digest_are_gates(self):
        config,release,manifest,sums=fixture()
        for changed in (dict(config,android_signer_sha256=None),dict(config,android_signer_sha256='e'*64),dict(config,minimum_android_version_code=203)):
            with self.assertRaises(ValueError):site.release_feed(changed,release,manifest,sums,'c'*40)
        with self.assertRaises(ValueError):site.release_feed(config,release,manifest,sums,'e'*40)
        wrong=copy.deepcopy(release);wrong['assets'][0]['digest']='sha256:'+'e'*64
        with self.assertRaises(ValueError):site.release_feed(config,wrong,manifest,sums,'c'*40)
    def test_source_archives_and_oversized_files_never_become_app_downloads(self):
        config,release,manifest,sums=fixture()
        with self.assertRaises(ValueError):site.release_feed(config,release,dict(manifest,contains_verified_apk=False),sums,'c'*40)
        wrong=copy.deepcopy(release);wrong['assets'][0]['size']=2**31
        with self.assertRaises(ValueError):site.release_feed(config,wrong,manifest,sums,'c'*40)
    def test_latest_returns_pending_only_for_no_stable_release_and_checks_metadata_checksum(self):
        config,release,manifest,sums=fixture();raw=site.canonical(manifest).encode()
        with patch.object(site,'api',return_value=None):self.assertEqual(site.latest(config)['status'],'pending')
        with patch.object(site,'api',side_effect=[release,{'sha':'c'*40}]),patch.object(site,'public_bytes',side_effect=[raw,sums.encode()]),patch.object(site,'verify_remote_apk') as verify:self.assertEqual(site.latest(config)['status'],'available');verify.assert_called_once()
        with patch.object(site,'api',return_value=release),patch.object(site,'public_bytes',side_effect=[raw+b' ',sums.encode()]):
            with self.assertRaises(ValueError):site.latest(config)
    def test_failed_live_validation_cannot_replace_a_working_site(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'new-public'
            with patch.object(site,'latest',side_effect=ValueError('invalid release')):
                with self.assertRaises(ValueError):site.build(ROOT,out,True)
            self.assertFalse(out.exists())
    def test_api_credentials_are_not_sent_to_public_asset_hosts_and_redirects_are_restricted(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'public metadata'
        with patch.dict(os.environ,{'GITHUB_TOKEN':'synthetic-secret'}),patch.object(site.urllib.request,'build_opener') as builder:
            builder.return_value.open.return_value=Response();site.public_bytes('https://github.com/damonlinyz/baby/releases/download/v0.2.2/release.json')
            request=builder.return_value.open.call_args[0][0];self.assertIsNone(request.get_header('Authorization'))
        for value in ('http://github.com/file','https://evil.invalid/file','https://user:password@github.com/file'):
            with self.assertRaises(ValueError):site.public_bytes(value)
        with self.assertRaises(ValueError):site.SecureRedirect().redirect_request(None,None,302,'',{},'https://evil.invalid/asset')
    def test_download_stream_is_bounded_and_digest_checked(self):
        import io
        data=b'synthetic APK bytes';url='https://github.com/damonlinyz/baby/releases/download/v0.2.2/baba-android.apk'
        with tempfile.TemporaryDirectory() as directory,patch.object(site.urllib.request,'build_opener') as opener:
            file=Path(directory)/'file.apk';opener.return_value.open.return_value=io.BytesIO(data)
            site.download_apk(url,file,len(data),hashlib.sha256(data).hexdigest());self.assertEqual(file.read_bytes(),data);file.unlink()
            for size,digest in ((len(data)-1,hashlib.sha256(data).hexdigest()),(len(data),'e'*64)):
                opener.return_value.open.return_value=io.BytesIO(data)
                with self.assertRaises(ValueError):site.download_apk(url,file,size,digest)
                self.assertFalse(file.exists())
    def test_remote_apk_is_remeasured_against_its_source_commit_not_only_json_claims(self):
        import base64
        config,release,manifest,_=fixture();source={'version':{'name':'0.2.2','code':202},'plus':{'distribute':{'android':{'packagename':'org.example.baba'}}}}
        response={'encoding':'base64','content':base64.b64encode(json.dumps(source).encode()).decode()}
        with patch.object(site,'api',return_value=response),patch.object(site,'download_apk') as download,patch.object(release_bundle,'inspect_apk',return_value=manifest['android']) as inspect:
            site.verify_remote_apk(config,release,manifest);download.assert_called_once();self.assertEqual(inspect.call_args[0][2],source)
            inspect.return_value=dict(manifest['android'],signer_sha256='e'*64)
            with self.assertRaises(ValueError):site.verify_remote_apk(config,release,manifest)


class ApkInspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.apk=Path(self.tmp.name)/'synthetic.apk';self.apk.write_bytes(b'synthetic fixture, SDK calls must be mocked')
        self.manifest={'version':{'name':'0.2.2','code':202},'plus':{'distribute':{'android':{'packagename':'org.example.baba'}}}}
        self.certs='Signer #1 certificate SHA-256 digest: '+'b'*64
        self.badging="package: name='org.example.baba' versionCode='202' versionName='0.2.2'\nsdkVersion:'23'"
    def test_inspection_pins_publisher_and_extracts_real_sdk_fields(self):
        with patch.object(release_bundle.shutil,'which',return_value='/synthetic/sdk'),patch.object(release_bundle,'run',side_effect=[self.certs,self.badging]):
            info=release_bundle.inspect_apk(ROOT,self.apk,self.manifest,'b'*64)
        self.assertEqual(info['min_sdk'],23);self.assertEqual(info['sha256'],release_bundle.checksum(self.apk));self.assertEqual(info['signer_sha256'],'b'*64)
    def test_unpinned_wrong_signer_bad_version_or_missing_sdk_is_rejected(self):
        with patch.object(release_bundle.shutil,'which',return_value='/synthetic/sdk'):
            with self.assertRaises(ValueError):release_bundle.inspect_apk(ROOT,self.apk,self.manifest,None)
            for certs,badging in [(self.certs.replace('b'*64,'e'*64),self.badging),(self.certs,self.badging.replace("202","201")),(self.certs,self.badging.splitlines()[0])]:
                with patch.object(release_bundle,'run',side_effect=[certs,badging]):
                    with self.assertRaises(ValueError):release_bundle.inspect_apk(ROOT,self.apk,self.manifest,'b'*64)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'website').mkdir();self.bundle=self.root/'bundle';self.bundle.mkdir()
        config,_,self.metadata,_=fixture();(self.root/'website/config.json').write_text(json.dumps(config))
        self.source={'version':{'name':'0.2.2','code':202}}
        self.notes=self.root/'notes.md';self.notes.write_text('合成发布说明')
        (self.bundle/'baba-source.zip').write_bytes(b'synthetic source');(self.bundle/'baba-android.apk').write_bytes(b'synthetic verified-by-mocked-sdk APK')
        self.metadata['android']['size_bytes']=(self.bundle/'baba-android.apk').stat().st_size;self.metadata['android']['sha256']=release_bundle.checksum(self.bundle/'baba-android.apk')
        self.save()
    def save(self):
        (self.bundle/'release.json').write_text(site.canonical(self.metadata));(self.bundle/'SHA256SUMS').write_text(''.join(release_bundle.checksum(self.bundle/name)+'  '+name+'\n' for name in ('baba-source.zip','baba-android.apk','release.json')))
    def test_publication_defaults_to_draft_and_requires_explicit_publish(self):
        with patch.object(release_bundle,'run',return_value=json.dumps(self.source)),patch.object(release_bundle,'inspect_apk',return_value=self.metadata['android']),patch.object(publish_release.subprocess,'run') as remote:
            result=publish_release.publish(self.root,self.bundle,self.notes);self.assertTrue(result['draft']);self.assertIn('--draft',remote.call_args[0][0]);self.assertNotIn('--latest',remote.call_args[0][0])
            publish_release.publish(self.root,self.bundle,self.notes,True);self.assertIn('--latest',remote.call_args[0][0])
    def test_dry_run_performs_checks_without_uploading(self):
        with patch.object(release_bundle,'run',return_value=json.dumps(self.source)),patch.object(release_bundle,'inspect_apk',return_value=self.metadata['android']),patch.object(publish_release.subprocess,'run') as remote:
            result=publish_release.publish(self.root,self.bundle,self.notes,dry_run=True);remote.assert_not_called();self.assertTrue(result['dry_run'])
    def test_tampered_artifact_or_source_only_bundle_is_never_uploaded(self):
        (self.bundle/'baba-android.apk').write_bytes(b'tampered')
        with patch.object(publish_release.subprocess,'run') as remote:
            with self.assertRaises(ValueError):publish_release.publish(self.root,self.bundle,self.notes)
            self.metadata['contains_verified_apk']=False;self.save()
            with self.assertRaises(ValueError):publish_release.publish(self.root,self.bundle,self.notes)
            remote.assert_not_called()
