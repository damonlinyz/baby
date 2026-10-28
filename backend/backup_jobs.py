#!/usr/bin/env python3
"""Encrypted local backup jobs, retention and freshness checks (Linux/OpenSSL)."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import tarfile
import tempfile
import time

import domain as d
import maintenance as m

VERSION = 1
KINDS = ('full', 'ledger')
RUN_ID = re.compile(r'\d{8}T\d{6}Z-[0-9a-f]{12}')
MANAGED_FILES = {'manifest.json', 'payload.cms'}
MAX_JSON = 65536
DEFAULT_MAX_BYTES = 1024 ** 3


def clock():
    return int(time.time())


def positive(value):
    if type(value) is not int or value < 1:
        raise m.MaintenanceError('数值参数须为正整数')
    return value


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, value):
    """Only metadata is published here; interrupted writes never replace status."""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.metadata-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON:
        raise m.MaintenanceError('备份元数据不存在或格式无效')
    try:
        value = json.loads(path.read_text())
    except (ValueError, UnicodeError) as exc:
        raise m.MaintenanceError('备份元数据格式无效') from exc
    if not isinstance(value, dict):
        raise m.MaintenanceError('备份元数据须为对象')
    return value


def private_directory(path, create=False):
    path = Path(path).absolute()
    if path.is_symlink():
        raise m.MaintenanceError('备份目录不能是符号链接')
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise m.MaintenanceError('请使用仅当前用户可访问的目录（权限 0700）')
    return path.resolve()


@contextlib.contextmanager
def job_lock(root):
    # Keep the inode: unlinking a lock file would let another process bypass it.
    fd = os.open(root / '.backup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise m.MaintenanceError('已有备份任务运行，本次未执行') from exc
        yield
    finally:
        os.close(fd)


def database_identity(database):
    with contextlib.closing(m.read_database(database)) as source:
        return m.identity(source)


def openssl(*args):
    try:
        result = subprocess.run(['openssl', *map(str, args)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise m.MaintenanceError('OpenSSL 不可用或操作超时') from exc
    if result.returncode:
        # OpenSSL diagnostics may contain local key paths; never publish them.
        raise m.MaintenanceError('OpenSSL 操作失败，请核对证书、私钥或加密文件')
    return result.stdout


def expected_files(kind):
    return {'deletions.sqlite3'} | ({'database.sqlite3'} if kind == 'full' else set())


def validate_manifest(value):
    if (value.get('version') != VERSION or value.get('kind') not in KINDS
            or not isinstance(value.get('run_id'), str) or not RUN_ID.fullmatch(value['run_id'])
            or not isinstance(value.get('instance_id'), str) or not value['instance_id']
            or type(value.get('created_at')) is not int or value['created_at'] < 1
            or not isinstance(value.get('files'), dict)):
        raise m.MaintenanceError('备份清单版本或字段不正确')
    if set(value['files']) != expected_files(value['kind']):
        raise m.MaintenanceError('备份文件范围不正确')
    for info in value['files'].values():
        if (not isinstance(info, dict) or type(info.get('size')) is not int or info['size'] < 1
                or not isinstance(info.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', info['sha256'])):
            raise m.MaintenanceError('备份文件校验信息无效')
    for field in ('encrypted_sha256', 'recipient_sha256'):
        if not isinstance(value.get(field), str) or not re.fullmatch('[0-9a-f]{64}', value[field]):
            raise m.MaintenanceError('加密备份校验信息无效')
    return value


def read_bundle(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise m.MaintenanceError('备份批次目录不存在或为符号链接')
    manifest = validate_manifest(read_json(directory / 'manifest.json'))
    payload = directory / 'payload.cms'
    if payload.is_symlink() or not payload.is_file() or m.checksum(payload) != manifest['encrypted_sha256']:
        raise m.MaintenanceError('加密备份校验失败')
    return manifest


def verify_snapshots(directory, manifest):
    for name, info in manifest['files'].items():
        path = directory / name
        if path.stat().st_size != info['size'] or m.checksum(path) != info['sha256']:
            raise m.MaintenanceError('解密后的数据库校验失败')
        with contextlib.closing(m.read_database(path)) as connection:
            m.check(connection)
            if m.identity(connection) != manifest['instance_id']:
                raise m.MaintenanceError('备份文件实例标识不匹配')
            if name == 'deletions.sqlite3':
                connection.execute('SELECT user_id,deleted_at FROM tombstones LIMIT 1').fetchall()
            else:
                connection.execute('SELECT id FROM users LIMIT 1').fetchall()
                connection.execute('SELECT kind FROM records LIMIT 1').fetchall()


def publish_directory(staged, target, marker):
    """Reserve the new directory exclusively; publish the completeness marker last."""
    target.mkdir(mode=0o700)
    published = []
    try:
        for source in sorted(staged.iterdir(), key=lambda p: p.name == marker):
            os.link(source, target / source.name)
            published.append(target / source.name)
        sync_directory(target)
        sync_directory(target.parent)
    except Exception:
        for path in published:
            path.unlink()
        target.rmdir()
        raise


def retain(root, kind, instance, keep, current_run):
    """Remove only complete, verified batches of this instance and exact format."""
    valid = []
    for path in (root / kind).iterdir():
        if not RUN_ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
            continue
        if {p.name for p in path.iterdir()} != MANAGED_FILES:
            continue
        try:
            manifest = read_bundle(path)
        except (m.MaintenanceError, OSError, ValueError):
            continue
        if manifest['instance_id'] == instance and manifest['kind'] == kind and manifest['run_id'] == path.name:
            valid.append((manifest['created_at'], path.name, path))
    if not any(item[1] == current_run for item in valid):
        raise m.MaintenanceError('新备份未通过发布后校验，未执行保留清理')
    removed = 0
    others = [item for item in sorted(valid, reverse=True) if item[1] != current_run]
    for _, _, path in others[keep - 1:]:
        for name in MANAGED_FILES:
            (path / name).unlink()
        path.rmdir()
        removed += 1
    sync_directory(root / kind)
    return removed


def run(database, directory, recipient_cert, kind='full', keep=14):
    if kind not in KINDS:
        raise m.MaintenanceError('备份类型无效')
    positive(keep)
    root = private_directory(directory, create=True)
    with job_lock(root):
        state_path = root / (kind + '.status.json')
        previous = read_json(state_path) if state_path.exists() else {}
        started = clock()
        run_id = datetime.datetime.fromtimestamp(started, datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(6)
        state = {'version': VERSION, 'kind': kind, 'state': 'running', 'started_at': started,
                 'run_id': run_id, 'last_success': previous.get('last_success')}
        write_json(state_path, state)
        try:
            instance = database_identity(database)
            binding = root / 'instance.json'
            if binding.exists():
                if read_json(binding).get('instance_id') != instance:
                    raise m.MaintenanceError('备份目录已绑定其他数据库实例')
            else:
                write_json(binding, {'version': VERSION, 'instance_id': instance})
            cert = Path(recipient_cert).resolve(strict=True)
            openssl('x509', '-in', cert, '-checkend', '0', '-noout')
            certificate_hash = hashlib.sha256(openssl('x509', '-in', cert, '-outform', 'DER')).hexdigest()
            batches = private_directory(root / kind, create=True)
            # Plaintext staging is private and outside the archive directory. systemd
            # PrivateTmp cleans it on abnormal service exit as well as normal cleanup.
            with tempfile.TemporaryDirectory(prefix='baba-backup-') as scratch:
                scratch = Path(scratch)
                files = {}
                # Snapshot business data first, then the monotonic deletion ledger.
                # A later deletion is replayable even if not in the business snapshot.
                sources = [('database.sqlite3', database)] if kind == 'full' else []
                sources.append(('deletions.sqlite3', d.deletion_ledger_path(database)))
                for name, source in sources:
                    result = m.backup(source, scratch / name)
                    if result['instance_id'] != instance:
                        raise m.MaintenanceError('业务数据库与删除记录实例不一致')
                    files[name] = {'size': (scratch / name).stat().st_size, 'sha256': result['sha256']}
                core = {'version': VERSION, 'kind': kind, 'run_id': run_id,
                        'instance_id': instance, 'created_at': started, 'files': files}
                verify_snapshots(scratch, core)
                write_json(scratch / 'snapshot.json', core)
                archive = scratch / 'snapshot.tar'
                fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as output, tarfile.open(fileobj=output, mode='w') as tar:
                    for name in ['snapshot.json', *files]:
                        tar.add(scratch / name, arcname=name, recursive=False)
                with tempfile.TemporaryDirectory(prefix='.pending-', dir=batches) as pending:
                    pending = Path(pending)
                    payload = pending / 'payload.cms'
                    payload.touch(mode=0o600)
                    openssl('cms', '-encrypt', '-binary', '-aes-256-gcm', '-outform', 'DER',
                            '-in', archive, '-out', payload, cert)
                    with payload.open('rb') as encrypted:
                        os.fsync(encrypted.fileno())
                    manifest = dict(core, recipient_sha256=certificate_hash, encrypted_sha256=m.checksum(payload))
                    write_json(pending / 'manifest.json', manifest)
                    read_bundle(pending)
                    target = batches / run_id
                    # Unique name; never reuse an existing batch.
                    if target.exists() or target.is_symlink():
                        raise m.MaintenanceError('备份批次已存在')
                    publish_directory(pending, target, 'manifest.json')
            removed = retain(root, kind, instance, keep, run_id)
            state.update(state='success', finished_at=clock(), last_success={'run_id': run_id, 'created_at': started,
                         'instance_id': instance, 'encrypted_sha256': manifest['encrypted_sha256']})
            write_json(state_path, state)
            return {'ok': True, 'operation': 'backup-run', 'kind': kind, 'run_id': run_id,
                    'directory': str(target), 'sha256': manifest['encrypted_sha256'], 'pruned': removed}
        except Exception:
            state.update(state='failed', finished_at=clock())
            write_json(state_path, state)
            raise


def status(database, directory, kind='full', max_age_hours=26, max_run_minutes=30):
    if kind not in KINDS:
        raise m.MaintenanceError('备份类型无效')
    positive(max_age_hours)
    positive(max_run_minutes)
    root = private_directory(directory)
    instance = database_identity(database)
    if read_json(root / 'instance.json').get('instance_id') != instance:
        raise m.MaintenanceError('备份目录与数据库实例不匹配')
    state = read_json(root / (kind + '.status.json'))
    now = clock()
    if (state.get('version') != VERSION or state.get('kind') != kind
            or state.get('state') not in ('success', 'running', 'failed')
            or type(state.get('started_at')) is not int or not 0 <= now - state['started_at']):
        raise m.MaintenanceError('备份任务状态不正确')
    if state['state'] == 'failed':
        raise m.MaintenanceError('最近一次备份失败')
    if state['state'] == 'running' and now - state['started_at'] > max_run_minutes * 60:
        raise m.MaintenanceError('备份任务超时或被中断')
    success = state.get('last_success')
    if (not isinstance(success, dict) or not isinstance(success.get('run_id'), str)
            or not RUN_ID.fullmatch(success['run_id']) or type(success.get('created_at')) is not int):
        raise m.MaintenanceError('尚无成功的备份')
    age = now - success['created_at']
    if not 0 <= age <= max_age_hours * 3600:
        raise m.MaintenanceError('最近成功备份已过期或系统时间异常')
    batches = private_directory(root / kind)
    manifest = read_bundle(batches / success['run_id'])
    if (manifest['instance_id'] != instance or manifest['kind'] != kind or manifest['run_id'] != success['run_id']
            or manifest['created_at'] != success['created_at'] or manifest['encrypted_sha256'] != success.get('encrypted_sha256')):
        raise m.MaintenanceError('成功状态与备份清单不匹配')
    return {'ok': True, 'operation': 'backup-status', 'kind': kind, 'state': state['state'],
            'run_id': success['run_id'], 'age_seconds': age}


def unpack(bundle, private_key, output, max_bytes=DEFAULT_MAX_BYTES, passphrase_env=None):
    """Decrypt to a new directory. Does not restore or select a deletion ledger."""
    positive(max_bytes)
    if passphrase_env is not None and (not isinstance(passphrase_env, str)
            or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', passphrase_env)):
        raise m.MaintenanceError('口令环境变量名无效')
    bundle = Path(bundle).resolve()
    manifest = read_bundle(bundle)
    if sum(info['size'] for info in manifest['files'].values()) > max_bytes:
        raise m.MaintenanceError('备份超过允许的解密大小')
    if (bundle / 'payload.cms').stat().st_size > max_bytes + 1024 ** 2:
        raise m.MaintenanceError('加密文件超过允许大小')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise m.MaintenanceError('解密目标已存在，请使用新目录')
    parent = private_directory(output.parent)
    with tempfile.TemporaryDirectory(prefix='.baba-unpack-', dir=parent) as temp:
        temp = Path(temp)
        archive = temp / 'decrypted.tar'
        archive.touch(mode=0o600)
        password = ['-passin', 'env:' + passphrase_env] if passphrase_env else []
        openssl('cms', '-decrypt', '-binary', '-inform', 'DER', '-in', bundle / 'payload.cms',
                '-inkey', Path(private_key).resolve(strict=True), '-out', archive, *password)
        extracted = temp / 'files'
        extracted.mkdir(mode=0o700)
        names = expected_files(manifest['kind']) | {'snapshot.json'}
        seen = set()
        total = 0
        # Never extractall: only fixed regular files, with no paths, links or duplicates.
        with tarfile.open(archive, 'r:') as tar:
            for entry in tar:
                if entry.name not in names or entry.name in seen or not entry.isfile() or entry.size < 1:
                    raise m.MaintenanceError('备份归档包含非预期文件')
                seen.add(entry.name)
                total += entry.size
                if total > max_bytes + MAX_JSON or (entry.name == 'snapshot.json' and entry.size > MAX_JSON):
                    raise m.MaintenanceError('解密归档超过允许大小')
                destination = extracted / entry.name
                fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as target, tar.extractfile(entry) as source:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
        if seen != names:
            raise m.MaintenanceError('备份归档缺少文件')
        core = read_json(extracted / 'snapshot.json')
        expected = {k: v for k, v in manifest.items() if k not in ('recipient_sha256', 'encrypted_sha256')}
        if core != expected:
            raise m.MaintenanceError('加密内外清单不一致')
        verify_snapshots(extracted, core)
        if output.exists() or output.is_symlink():
            raise m.MaintenanceError('解密目标已存在')
        publish_directory(extracted, output, 'snapshot.json')
    return {'ok': True, 'operation': 'backup-unpack', 'kind': manifest['kind'],
            'instance_id': manifest['instance_id'], 'directory': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    job = commands.add_parser('run')
    health = commands.add_parser('status')
    for command in (job, health):
        command.add_argument('--db', required=True)
        command.add_argument('--directory', required=True)
        command.add_argument('--kind', choices=KINDS, default='full')
    job.add_argument('--recipient-cert', required=True)
    job.add_argument('--keep', type=int, default=14)
    health.add_argument('--max-age-hours', type=int, default=26)
    health.add_argument('--max-run-minutes', type=int, default=30)
    decrypt = commands.add_parser('unpack')
    decrypt.add_argument('--bundle', required=True)
    decrypt.add_argument('--private-key', required=True)
    decrypt.add_argument('--output', required=True)
    decrypt.add_argument('--max-bytes', type=int, default=DEFAULT_MAX_BYTES)
    decrypt.add_argument('--passphrase-env', help='已加密私钥的口令环境变量名；不传入口令值')
    args = vars(parser.parse_args())
    command = args.pop('command')
    try:
        if command == 'run':
            args['database'] = args.pop('db')
            result = run(**args)
        elif command == 'status':
            args['database'] = args.pop('db')
            result = status(**args)
        else:
            result = unpack(**args)
        print(json.dumps(result, ensure_ascii=False))
    except (m.MaintenanceError, sqlite3.Error, OSError, tarfile.TarError, ValueError) as exc:
        # No health data, certificate content, subprocess output or local secret path.
        message = str(exc) if isinstance(exc, m.MaintenanceError) else '本机文件或数据库操作失败'
        print(json.dumps({'ok': False, 'operation': 'backup-' + command, 'error': message}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
