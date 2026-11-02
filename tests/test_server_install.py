import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('server_install',ROOT/'tools/server_install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class ServerInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name in ('CODE','DATA','CONFIG','UNIT','LOCK'):
            path=self.root/name.lower()
            if name=='UNIT':path=self.root/'systemd/baba.service';path.parent.mkdir()
            context=patch.object(installer,name,path);context.start();self.addCleanup(context.stop)
        self.commit='a'*40;self.domain='baba.example.com'

    def test_preview_is_non_mutating_and_publication_requires_separate_caddy_step(self):
        before=set(self.root.rglob('*'))
        with patch.object(installer,'run') as commands:
            plan=installer.plan(self.commit,self.domain)
        commands.assert_not_called();self.assertEqual(set(self.root.rglob('*')),before)
        self.assertFalse(plan['activates_caddy']);self.assertFalse(plan['opens_firewall'])
        result=subprocess.run([sys.executable,str(ROOT/'tools/server_install.py'),'--revision',self.commit,'--domain',self.domain],check=True,text=True,capture_output=True)
        self.assertEqual(json.loads(result.stdout)['mode'],'plan')

    def test_invalid_domain_or_mutable_source_is_rejected_before_any_system_command(self):
        with patch.object(installer,'run') as commands:
            for domain in ('example.com\nHOST=0.0.0.0','https://example.com','example.com:80','*.example.com','example.com/x','Example.com','127.0.0.1','x;id.com','-bad.example.com'):
                with self.subTest(domain=domain),self.assertRaises(ValueError):installer.plan(self.commit,domain)
            for revision in ('main','v0.2.2','a'*39,'a'*40+';id'):
                with self.assertRaises(ValueError):installer.plan(revision,self.domain)
        commands.assert_not_called()

    def test_existing_paths_and_even_dangling_links_are_never_replaced(self):
        for name in ('CODE','DATA','CONFIG','UNIT'):
            path=getattr(installer,name);path.symlink_to(self.root/'not-created')
            with self.assertRaises(ValueError):installer.new_paths()
            self.assertTrue(path.is_symlink());path.unlink()
        installer.DATA.mkdir();(installer.DATA/'important').write_text('existing data')
        with self.assertRaises(ValueError):installer.new_paths()
        self.assertEqual((installer.DATA/'important').read_text(),'existing data')

    def mocks(self):
        def checkout(path,revision):
            path.mkdir();(path/'source-commit').write_text(revision)
        return (patch.object(installer,'preflight',side_effect=installer.new_paths),
                patch.object(installer,'checkout',side_effect=checkout),
                patch.object(installer,'run'),
                patch.object(installer.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=12345,pw_gid=12345)),
                patch.object(installer.os,'chown'))

    def test_first_install_creates_separate_private_data_and_never_controls_existing_services(self):
        first,second,third,fourth,fifth=self.mocks()
        with first,second,third as commands,fourth,fifth,patch.object(installer,'healthy',return_value=True):
            result=installer.install(self.commit,self.domain)
        self.assertEqual(result['health'],'passed')
        self.assertEqual((installer.CODE/'source-commit').read_text(),self.commit)
        self.assertEqual((installer.CONFIG/'service.env').stat().st_mode&0o777,0o600)
        self.assertEqual(installer.DATA.stat().st_mode&0o777,0o700)
        self.assertEqual((installer.DATA/'private').stat().st_mode&0o777,0o700)
        env=(installer.CONFIG/'service.env').read_text()
        for expected in ('HOST=127.0.0.1','PAYMENTS_ENABLED=0','WX_NOTIFY_ENABLED=0','AI_API_KEY=','PUBLIC_ORIGIN=https://baba.example.com'):self.assertIn(expected,env)
        self.assertIn('User=baba',installer.UNIT.read_text());self.assertIn('ProtectSystem=strict',installer.UNIT.read_text())
        system_calls=[c.args for c in commands.call_args_list if c.args[0]=='systemctl']
        self.assertEqual(system_calls,[('systemctl','daemon-reload'),('systemctl','start','baba.service'),('systemctl','enable','baba.service')])
        self.assertTrue((installer.CONFIG/'Caddyfile.fragment').is_file())

    def test_health_failure_stops_only_the_new_service_preserves_data_and_prevents_reinstallation(self):
        first,second,third,fourth,fifth=self.mocks()
        def unhealthy():
            (installer.DATA/'private/database-placeholder').write_text('preserve me');return False
        with first,second,third as commands,fourth,fifth,patch.object(installer,'healthy',side_effect=unhealthy),patch.object(installer.subprocess,'run') as stop:
            with self.assertRaises(ValueError):installer.install(self.commit,self.domain)
            stop.assert_called_once_with(['systemctl','stop','baba.service'],check=False,capture_output=True)
        self.assertNotIn(('systemctl','enable','baba.service'),[c.args for c in commands.call_args_list])
        self.assertEqual((installer.DATA/'private/database-placeholder').read_text(),'preserve me')
        with self.assertRaises(ValueError):installer.new_paths()

    def test_source_mismatch_or_links_are_rejected_before_persistent_installation(self):
        target=self.root/'download'
        with patch.object(installer,'run',return_value='b'*40):
            with self.assertRaises(ValueError):installer.checkout(target,self.commit)
        def output(*args):
            if args[-2:]==('rev-parse','HEAD'):return self.commit
            if 'ls-tree' in args:return '120000 blob '+self.commit+'\tunsafe-link'
            return ''
        with patch.object(installer,'run',side_effect=output):
            with self.assertRaises(ValueError):installer.checkout(target,self.commit)
        self.assertFalse(installer.CODE.exists());self.assertFalse(installer.DATA.exists())

    def test_non_root_apply_is_refused_before_system_inspection(self):
        with patch.object(installer.os,'geteuid',return_value=1000),patch.object(installer,'run') as commands:
            with self.assertRaises(ValueError):installer.preflight()
        commands.assert_not_called();self.assertFalse(installer.CODE.exists())


if __name__=='__main__':unittest.main()
