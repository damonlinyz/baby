import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import test_domain
import domain as d
import maintenance as m
import backup_jobs as jobs


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL is required for encrypted backup tests')
class BackupJobTests(unittest.TestCase):
    setUp = test_domain.DomainTests.setUp
    tearDown = test_domain.DomainTests.tearDown
    call = test_domain.DomainTests.call

    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys.cleanup)
        cls.cert = Path(cls.keys.name) / 'recipient.pem'
        cls.key = Path(cls.keys.name) / 'private.pem'
        cls.wrong_key = Path(cls.keys.name) / 'other.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                        '-subj', '/CN=Synthetic backup test/', '-keyout', str(cls.key), '-out', str(cls.cert)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048',
                        '-out', str(cls.wrong_key)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def root(self):
        return Path(self.tmp.name) / 'encrypted'

    def run_job(self, kind='full', keep=14):
        return jobs.run(d.DB, self.root(), self.cert, kind, keep)

    def unpack(self, result, name='unpacked'):
        target = Path(self.tmp.name) / name
        jobs.unpack(result['directory'], self.key, target)
        return target

    def connect(self, path):
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        return connection

    def test_encrypted_full_backup_roundtrip_and_private_permissions(self):
        result = self.run_job()
        batch = Path(result['directory'])
        self.assertEqual({p.name for p in batch.iterdir()}, jobs.MANAGED_FILES)
        self.assertNotIn(b'SQLite format 3', (batch / 'payload.cms').read_bytes())
        for path in (self.root(), batch.parent, batch):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        for path in batch.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        target = self.unpack(result)
        self.assertEqual(self.connect(target / 'database.sqlite3').execute('SELECT count(*) FROM users').fetchone()[0], 3)
        self.assertEqual(self.connect(target / 'deletions.sqlite3').execute('SELECT count(*) FROM tombstones').fetchone()[0], 0)
        self.assertTrue(jobs.status(d.DB, self.root())['ok'])
        for path in target.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_separate_later_ledger_replays_deleted_mother_and_baby_history(self):
        self.call(self.mom, 'pregnancy.create', {'consent': True})
        with d.transaction() as c:
            p = d.snapshot(c, self.mom, self.fid)['pregnancy']
        self.call(self.mom, 'pregnancy.event', {'id': p['id'], 'version': p['version'], 'event': 'set_edd',
                  'date': str(d.today() + datetime.timedelta(days=100))})
        with d.transaction() as c:
            p = d.snapshot(c, self.mom, self.fid)['pregnancy']
        self.call(self.mom, 'pregnancy.event', {'id': p['id'], 'version': p['version'], 'event': 'birth',
                  'date': str(d.today()), 'babies': 2, 'birth_outcomes': ['growing', 'hospitalized'], 'guardian_consent': True})
        full = self.run_job()
        with d.transaction() as c:
            d.delete_account(c, self.mom, 'long-password-123')
        ledger = self.run_job('ledger')
        old = self.unpack(full, 'old-business')
        recent = self.unpack(ledger, 'recent-deletions')
        self.assertFalse((recent / 'database.sqlite3').exists())
        output = Path(self.tmp.name) / 'restored.sqlite3'
        result = m.restore(old / 'database.sqlite3', recent / 'deletions.sqlite3', output)
        self.assertEqual(result['replayed_deletions'], 1)
        c = self.connect(output)
        self.assertFalse(c.execute('SELECT 1 FROM users WHERE id=?', (self.mom,)).fetchone())
        self.assertEqual(d.rows(c, 'baby'), [])
        self.assertEqual(d.rows(c, 'baby_event'), [])
        self.assertTrue(jobs.status(d.DB, self.root(), 'ledger', 1)['ok'])

    def test_retention_preserves_current_run_when_timestamps_tie(self):
        with patch.object(jobs, 'clock', return_value=1800000000), patch.object(jobs.secrets, 'token_hex',
                    side_effect=['ffffffffffff', '000000000000']):
            first = self.run_job(keep=1)
            second = self.run_job(keep=1)
            self.assertEqual(second['pruned'], 1)
            self.assertFalse(Path(first['directory']).exists())
            self.assertTrue(jobs.status(d.DB, self.root())['ok'])

    def test_full_and_ledger_retention_are_independent_and_preserve_unknown_files(self):
        full = self.run_job()
        first = self.run_job('ledger')
        foreign = self.root() / 'ledger' / '20000101T000000Z-000000000000'
        foreign.mkdir()
        (foreign / 'do-not-delete.txt').write_text('user file')
        second = self.run_job('ledger', keep=1)
        self.assertEqual(second['pruned'], 1)
        self.assertTrue(Path(full['directory']).exists())
        self.assertFalse(Path(first['directory']).exists())
        self.assertEqual((foreign / 'do-not-delete.txt').read_text(), 'user file')

    def test_failed_encryption_preserves_old_archive_and_marks_job_failed(self):
        first = self.run_job()
        actual = jobs.openssl
        def fail(*args):
            if args[:2] == ('cms', '-encrypt'):
                raise m.MaintenanceError('synthetic encryption failure')
            return actual(*args)
        with patch.object(jobs, 'openssl', side_effect=fail):
            with self.assertRaises(m.MaintenanceError):
                self.run_job(keep=1)
        self.assertTrue(Path(first['directory']).exists())
        self.assertEqual(len(list((self.root() / 'full').iterdir())), 1)
        self.assertEqual(jobs.read_json(self.root() / 'full.status.json')['state'], 'failed')
        with self.assertRaisesRegex(m.MaintenanceError, '失败'):
            jobs.status(d.DB, self.root())
        self.run_job()
        self.assertTrue(jobs.status(d.DB, self.root())['ok'])

    def test_stale_and_future_clocks_fail_health_check(self):
        self.run_job()
        with patch.object(jobs, 'clock', return_value=jobs.clock() + 27 * 3600):
            with self.assertRaisesRegex(m.MaintenanceError, '过期'):
                jobs.status(d.DB, self.root())
        with patch.object(jobs, 'clock', return_value=1):
            with self.assertRaises(m.MaintenanceError):
                jobs.status(d.DB, self.root())

    def test_running_job_uses_prior_success_only_until_timeout(self):
        self.run_job()
        path = self.root() / 'full.status.json'
        state = jobs.read_json(path)
        state.update(state='running', started_at=jobs.clock())
        jobs.write_json(path, state)
        self.assertEqual(jobs.status(d.DB, self.root())['state'], 'running')
        with patch.object(jobs, 'clock', return_value=state['started_at'] + 1801):
            with self.assertRaisesRegex(m.MaintenanceError, '中断'):
                jobs.status(d.DB, self.root())

    def test_lock_prevents_overlapping_jobs_without_rewriting_status(self):
        self.run_job()
        path = self.root() / 'full.status.json'
        original = path.read_bytes()
        with jobs.job_lock(self.root()):
            with self.assertRaisesRegex(m.MaintenanceError, '已有备份任务'):
                self.run_job()
        self.assertEqual(path.read_bytes(), original)

    def test_database_instance_mismatch_never_deletes_existing_backups(self):
        first = self.run_job()
        with d.transaction() as c:
            c.execute("UPDATE metadata SET value='other-instance' WHERE key='instance_id'")
        with self.assertRaisesRegex(m.MaintenanceError, '其他数据库实例'):
            self.run_job(keep=1)
        self.assertTrue(Path(first['directory']).exists())
        with self.assertRaises(m.MaintenanceError):
            jobs.status(d.DB, self.root())

    def test_bad_or_missing_ledger_never_publishes_partial_full_backup(self):
        ledger = d.deletion_ledger_path()
        with contextlib.closing(sqlite3.connect(ledger)) as c:
            c.execute("UPDATE metadata SET value='wrong' WHERE key='instance_id'")
            c.commit()
        with self.assertRaises(m.MaintenanceError):
            self.run_job()
        self.assertEqual(list((self.root() / 'full').iterdir()), [])
        ledger.unlink()
        with self.assertRaises(m.MaintenanceError):
            self.run_job('ledger')

    def test_wrong_key_and_tampered_ciphertext_publish_no_plaintext(self):
        result = self.run_job()
        target = Path(self.tmp.name) / 'should-not-exist'
        with self.assertRaises(m.MaintenanceError):
            jobs.unpack(result['directory'], self.wrong_key, target)
        self.assertFalse(target.exists())
        batch = Path(result['directory'])
        payload = batch / 'payload.cms'
        value = bytearray(payload.read_bytes())
        value[-1] ^= 1
        payload.write_bytes(value)
        # Even when the outer checksum is recomputed, GCM authentication fails.
        manifest = jobs.read_json(batch / 'manifest.json')
        manifest['encrypted_sha256'] = m.checksum(payload)
        jobs.write_json(batch / 'manifest.json', manifest)
        with self.assertRaises(m.MaintenanceError):
            jobs.unpack(batch, self.key, target)
        self.assertFalse(target.exists())
        self.assertFalse(list(Path(self.tmp.name).glob('.baba-unpack-*')))

    def test_missing_and_corrupt_success_artifacts_are_unhealthy(self):
        result = self.run_job()
        batch = Path(result['directory'])
        (batch / 'payload.cms').write_bytes(b'corrupted')
        with self.assertRaisesRegex(m.MaintenanceError, '校验失败'):
            jobs.status(d.DB, self.root())
        (batch / 'manifest.json').unlink()
        with self.assertRaises(m.MaintenanceError):
            jobs.status(d.DB, self.root())

    def test_outer_metadata_cannot_change_internal_instance_or_date(self):
        result = self.run_job()
        batch = Path(result['directory'])
        manifest = jobs.read_json(batch / 'manifest.json')
        manifest['created_at'] += 1
        jobs.write_json(batch / 'manifest.json', manifest)
        with self.assertRaisesRegex(m.MaintenanceError, '内外清单'):
            self.unpack(result)
        self.assertFalse((Path(self.tmp.name) / 'unpacked').exists())

    def test_existing_output_size_limits_and_symlinks_are_rejected(self):
        result = self.run_job()
        output = Path(self.tmp.name) / 'existing'
        output.mkdir()
        (output / 'keep').write_text('keep')
        with self.assertRaises(m.MaintenanceError):
            jobs.unpack(result['directory'], self.key, output)
        self.assertEqual((output / 'keep').read_text(), 'keep')
        with self.assertRaisesRegex(m.MaintenanceError, '大小'):
            jobs.unpack(result['directory'], self.key, Path(self.tmp.name) / 'too-large', 1)
        link = Path(self.tmp.name) / 'linked-root'
        link.symlink_to(self.root(), target_is_directory=True)
        with self.assertRaises(m.MaintenanceError):
            jobs.run(d.DB, link, self.cert)

    def test_invalid_retention_and_public_directory_are_rejected(self):
        for value in (0, -1, True):
            with self.assertRaises(m.MaintenanceError):
                self.run_job(keep=value)
        self.root().mkdir(mode=0o755)
        self.root().chmod(0o755)
        with self.assertRaisesRegex(m.MaintenanceError, '0700'):
            self.run_job()

    def test_cli_success_and_nonzero_monitoring_failure(self):
        command = [sys.executable, str(Path(jobs.__file__)), 'run', '--db', str(d.DB),
                   '--directory', str(self.root()), '--recipient-cert', str(self.cert)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        health = command[:2] + ['status', '--db', str(d.DB), '--directory', str(self.root())]
        self.assertEqual(subprocess.run(health, capture_output=True).returncode, 0)
        state = self.root() / 'full.status.json'
        value = jobs.read_json(state)
        value['state'] = 'failed'
        jobs.write_json(state, value)
        result = subprocess.run(health, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['ok'])

    def test_unsafe_tar_entries_and_duplicate_names_are_rejected(self):
        result = self.run_job()
        batch = Path(result['directory'])
        manifest = jobs.read_json(batch / 'manifest.json')
        for name, kind in (('../escaped', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                           ('database.sqlite3', tarfile.SYMTYPE), ('duplicate', tarfile.REGTYPE)):
            with self.subTest(name=name, kind=kind):
                archive = Path(self.tmp.name) / 'forged.tar'
                with tarfile.open(archive, 'w') as tar:
                    for _ in range(2 if name == 'duplicate' else 1):
                        info = tarfile.TarInfo('database.sqlite3' if name == 'duplicate' else name)
                        info.type = kind
                        info.size = 1 if kind == tarfile.REGTYPE else 0
                        info.linkname = '../escaped' if kind == tarfile.SYMTYPE else ''
                        tar.addfile(info, io.BytesIO(b'x') if info.size else None)
                jobs.openssl('cms', '-encrypt', '-binary', '-aes-256-gcm', '-outform', 'DER',
                             '-in', archive, '-out', batch / 'payload.cms', self.cert)
                manifest['encrypted_sha256'] = m.checksum(batch / 'payload.cms')
                jobs.write_json(batch / 'manifest.json', manifest)
                with self.assertRaises(m.MaintenanceError):
                    self.unpack(result)
                self.assertFalse((Path(self.tmp.name) / 'unpacked').exists())
                self.assertFalse((Path(self.tmp.name) / 'escaped').exists())

    def test_mid_publication_failure_removes_only_new_batch(self):
        first = self.run_job()
        original = os.link
        calls = 0
        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('synthetic disk failure')
            return original(source, target)
        with patch.object(jobs.os, 'link', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.run_job(keep=1)
        self.assertTrue(Path(first['directory']).exists())
        self.assertEqual(len(list((self.root() / 'full').iterdir())), 1)
        self.assertEqual(jobs.read_json(self.root() / 'full.status.json')['state'], 'failed')

    def test_success_status_rejects_path_traversal_and_new_target_cannot_be_overwritten(self):
        self.run_job()
        path = self.root() / 'full.status.json'
        state = jobs.read_json(path)
        state['last_success']['run_id'] = '../../private'
        jobs.write_json(path, state)
        with self.assertRaises(m.MaintenanceError):
            jobs.status(d.DB, self.root())
        staged = Path(self.tmp.name) / 'staged'
        staged.mkdir()
        (staged / 'marker').write_text('new')
        target = Path(self.tmp.name) / 'already-there'
        target.mkdir()
        with self.assertRaises(FileExistsError):
            jobs.publish_directory(staged, target, 'marker')
        self.assertEqual(list(target.iterdir()), [])

    def test_password_protected_recovery_key_uses_environment_selector(self):
        result = self.run_job()
        protected = Path(self.tmp.name) / 'password-protected.pem'
        with patch.dict(os.environ, {'BABA_TEST_RECOVERY_PASSWORD': 'synthetic-only-password'}):
            jobs.openssl('pkey', '-in', self.key, '-aes256', '-passout', 'env:BABA_TEST_RECOVERY_PASSWORD',
                         '-out', protected)
            target = Path(self.tmp.name) / 'encrypted-key-recovery'
            jobs.unpack(result['directory'], protected, target, passphrase_env='BABA_TEST_RECOVERY_PASSWORD')
            self.assertTrue((target / 'database.sqlite3').is_file())
            with patch.dict(os.environ, {'BABA_TEST_RECOVERY_PASSWORD': 'wrong-synthetic-password'}):
                output = Path(self.tmp.name) / 'wrong-password'
                with self.assertRaises(m.MaintenanceError):
                    jobs.unpack(result['directory'], protected, output, passphrase_env='BABA_TEST_RECOVERY_PASSWORD')
                self.assertFalse(output.exists())

    def test_new_archive_corrupted_after_publication_cannot_trigger_retention(self):
        first = self.run_job()
        original_hash = m.checksum(Path(first['directory']) / 'payload.cms')
        original = jobs.publish_directory
        def corrupt(staged, target, marker):
            original(staged, target, marker)
            (target / 'payload.cms').write_bytes(b'synthetic storage corruption')
        with patch.object(jobs, 'publish_directory', side_effect=corrupt):
            with self.assertRaisesRegex(m.MaintenanceError, '发布后校验'):
                self.run_job(keep=1)
        self.assertEqual(m.checksum(Path(first['directory']) / 'payload.cms'), original_hash)
        self.assertEqual(jobs.read_json(self.root() / 'full.status.json')['state'], 'failed')
