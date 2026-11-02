import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tempfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('server_proxy',ROOT/'tools/server_proxy.py')
proxy=importlib.util.module_from_spec(spec);spec.loader.exec_module(proxy)


class ServerProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.domain='baba.example.com';self.address='163.7.5.202'
        for name,filename in (('CONFIG','Caddyfile'),('FRAGMENT','baba-app.caddy'),('METADATA','installed.json'),('LOCK','deployment.lock')):
            context=patch.object(proxy,name,self.root/filename);context.start();self.addCleanup(context.stop)
        self.before=b'# Existing private service; preserve this exact content.\nold.example.com {\n reverse_proxy 127.0.0.1:8081\n}\n'
        proxy.CONFIG.write_bytes(self.before);proxy.CONFIG.chmod(0o640)
        proxy.METADATA.write_text(json.dumps({'domain':self.domain}))

    def permissions(self):
        real=os.fstat
        # Only the privileged lock ownership check is simulated. Real file I/O and
        # permissions, staging, replacement and rollback use the temporary directory.
        return patch.object(proxy.os,'geteuid',return_value=0),patch.object(proxy.os,'fstat',side_effect=lambda fd:SimpleNamespace(st_mode=real(fd).st_mode,st_uid=0))

    def test_preview_never_reads_server_state_and_rejects_injection(self):
        with patch.object(proxy,'preflight') as checks,patch.object(proxy,'run') as commands:
            result=proxy.plan(self.domain,self.address)
            for domain in ('example.com\nroot /etc','https://example.com','example.com:80','*.example.com','example.com/other','-bad.example.com'):
                with self.assertRaises(ValueError):proxy.plan(domain,self.address)
            for address in ('127.0.0.1','172.31.6.251','163.7.5.202;id','not-an-address'):
                with self.assertRaises(ValueError):proxy.plan(self.domain,address)
        checks.assert_not_called();commands.assert_not_called()
        self.assertFalse(result['modifies_database']);self.assertEqual(proxy.CONFIG.read_bytes(),self.before)
        self.assertFalse(proxy.FRAGMENT.exists())

    def test_dns_mismatch_ipv6_and_unresolved_names_stop_before_file_changes(self):
        def answer(address):return (0,0,0,'',(address,443))
        with patch.object(proxy.socket,'getaddrinfo',return_value=[answer(self.address)]):proxy.dns(self.domain,self.address)
        for answers in ([answer('163.7.6.64')],[answer(self.address),answer('2001:4860:4860::8888')]):
            with patch.object(proxy.socket,'getaddrinfo',return_value=answers),self.assertRaises(ValueError):proxy.dns(self.domain,self.address)
        with patch.object(proxy.socket,'getaddrinfo',side_effect=OSError()),self.assertRaises(ValueError):proxy.dns(self.domain,self.address)
        self.assertEqual(proxy.CONFIG.read_bytes(),self.before);self.assertFalse(proxy.FRAGMENT.exists())

    def test_success_preserves_existing_sites_permissions_and_repeat_does_not_duplicate(self):
        first,second=self.permissions()
        def adapted(*args):return json.dumps({'match':[{'host':[self.domain]}]}) if proxy.FRAGMENT.exists() else '{}'
        with first,second,patch.object(proxy,'preflight',return_value='caddy'),patch.object(proxy,'caddy_command',side_effect=adapted),patch.object(proxy,'run') as commands,patch.object(proxy,'https',return_value='passed'):
            result=proxy.activate(self.domain,self.address)
            repeated=proxy.activate(self.domain,self.address)
        self.assertEqual(result['mode'],'configured');self.assertEqual(result['https'],'passed')
        self.assertEqual(repeated['mode'],'already_configured')
        text=proxy.CONFIG.read_bytes();self.assertTrue(text.startswith(self.before))
        self.assertEqual(text.count(('import '+str(proxy.FRAGMENT)).encode()),1)
        self.assertEqual(proxy.CONFIG.stat().st_mode&0o777,0o640)
        backup=Path(result['backup']);self.assertEqual(backup.read_bytes(),self.before);self.assertEqual(backup.stat().st_mode&0o777,0o600)
        self.assertEqual([x.args for x in commands.call_args_list],[('systemctl','reload','caddy.service')]*2)

    def test_failed_full_validation_leaves_running_config_and_all_existing_bytes_untouched(self):
        first,second=self.permissions()
        def caddy(*args):
            if args[1]=='validate':raise ValueError('synthetic validation failure')
            return '{}'
        with first,second,patch.object(proxy,'preflight',return_value='caddy'),patch.object(proxy,'caddy_command',side_effect=caddy),patch.object(proxy,'run') as commands:
            with self.assertRaises(ValueError):proxy.activate(self.domain,self.address)
        commands.assert_not_called();self.assertEqual(proxy.CONFIG.read_bytes(),self.before)
        self.assertFalse(proxy.FRAGMENT.exists());self.assertEqual(len(list(self.root.glob('.baba-caddy-*'))),0)

    def test_failed_reload_restores_original_and_reload_failure_is_reported_without_destroying_backup(self):
        for recovery_fails in (False,True):
            first,second=self.permissions()
            errors=[ValueError('synthetic reload failure'),ValueError('synthetic recovery failure') if recovery_fails else '']
            with first,second,patch.object(proxy,'preflight',return_value='caddy'),patch.object(proxy,'caddy_command',return_value='{}'),patch.object(proxy,'run',side_effect=errors) as commands:
                with self.assertRaises(ValueError) as error:proxy.activate(self.domain,self.address)
            self.assertEqual(commands.call_count,2);self.assertEqual(proxy.CONFIG.read_bytes(),self.before)
            self.assertFalse(proxy.FRAGMENT.exists())
            if recovery_fails:self.assertIn('自动恢复未完全确认',str(error.exception))
        self.assertTrue(all(p.read_bytes()==self.before for p in self.root.glob('Caddyfile.before-baba-*')))

    def test_existing_unmanaged_site_or_fragment_is_never_overwritten(self):
        first,second=self.permissions()
        with first,second,patch.object(proxy,'preflight',return_value='caddy'),patch.object(proxy,'caddy_command',return_value=json.dumps({'match':[{'host':[self.domain]}]})),patch.object(proxy,'run') as commands:
            with self.assertRaises(ValueError):proxy.activate(self.domain,self.address)
        commands.assert_not_called();self.assertEqual(proxy.CONFIG.read_bytes(),self.before)
        proxy.FRAGMENT.write_text('existing configuration')
        first,second=self.permissions()
        with first,second,patch.object(proxy,'preflight',return_value='caddy'),patch.object(proxy,'caddy_command',return_value='{}'),patch.object(proxy,'run') as commands:
            with self.assertRaises(ValueError):proxy.activate(self.domain,self.address)
        self.assertEqual(proxy.FRAGMENT.read_text(),'existing configuration');commands.assert_not_called()

    def test_https_pending_does_not_undo_a_valid_proxy_or_claim_it_has_passed(self):
        first,second=self.permissions()
        with first,second,patch.object(proxy,'preflight',return_value='caddy'),patch.object(proxy,'caddy_command',return_value='{}'),patch.object(proxy,'run'),patch.object(proxy,'https',return_value='pending'):
            result=proxy.activate(self.domain,self.address)
        self.assertEqual(result['proxy'],'reloaded');self.assertEqual(result['https'],'pending');self.assertTrue(proxy.FRAGMENT.exists())

    def test_concurrent_edit_is_preserved_and_never_reloaded_by_this_tool(self):
        first,second=self.permissions()
        def caddy(*args):
            if args[1]=='validate':proxy.CONFIG.write_bytes(b'# Concurrent administrator change\n')
            return '{}'
        with first,second,patch.object(proxy,'preflight',return_value='caddy'),patch.object(proxy,'caddy_command',side_effect=caddy),patch.object(proxy,'run') as commands:
            with self.assertRaises(ValueError):proxy.activate(self.domain,self.address)
        self.assertEqual(proxy.CONFIG.read_bytes(),b'# Concurrent administrator change\n');commands.assert_not_called()
        self.assertFalse(proxy.FRAGMENT.exists())

    def test_read_only_verification_does_not_reload_or_modify_config(self):
        with patch.object(proxy,'dns'),patch.object(proxy,'health',return_value=True),patch.object(proxy,'https',return_value='passed'),patch.object(proxy,'run') as commands:
            result=proxy.verify(self.domain,self.address)
        self.assertEqual(result['mode'],'verify');self.assertEqual(result['https'],'passed');commands.assert_not_called()
        self.assertEqual(proxy.CONFIG.read_bytes(),self.before);self.assertFalse(proxy.FRAGMENT.exists())


if __name__=='__main__':unittest.main()
