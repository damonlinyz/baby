#!/usr/bin/env python3
"""Offline SQLite backup/recovery. Never overwrites an existing destination."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import domain as d


class MaintenanceError(Exception):
    pass


def read_database(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise MaintenanceError('数据库文件不存在')
    c = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def check(c):
    if [r[0] for r in c.execute('PRAGMA integrity_check')] != ['ok']:
        raise MaintenanceError('SQLite 完整性检查失败')
    if c.execute('PRAGMA foreign_key_check').fetchone():
        raise MaintenanceError('数据库存在外键引用错误')


def identity(c):
    row = c.execute("SELECT value FROM metadata WHERE key='instance_id'").fetchone()
    if not row:
        raise MaintenanceError('数据库缺少实例标识，请先用新版服务完成初始化')
    return row[0]


@contextlib.contextmanager
def staging(output):
    output = Path(output).resolve()
    if output.exists() or output.is_symlink():
        raise MaintenanceError('输出文件已存在，请使用新文件名')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.baba-recovery-', suffix='.sqlite3', dir=output.parent)
    os.close(fd)
    try:
        yield Path(name), output
    finally:
        for suffix in ('', '-journal', '-wal', '-shm'):
            Path(name + suffix).unlink(missing_ok=True)


def publish(staged, output):
    os.chmod(staged, 0o600)
    with staged.open('rb') as f:
        os.fsync(f.fileno())
    # A hard link publishes atomically and refuses a destination created meanwhile.
    os.link(staged, output)


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def backup(database, output):
    with staging(output) as (temp, target):
        with contextlib.closing(read_database(database)) as source:
            check(source)
            instance = identity(source)
            with contextlib.closing(sqlite3.connect(temp)) as copy:
                source.backup(copy)
                copy.execute("INSERT OR REPLACE INTO metadata VALUES('backup_created_at',?)", (str(d.now()),))
                copy.commit()
                check(copy)
        publish(temp, target)
    return {'ok': True, 'operation': 'backup', 'instance_id': instance,
            'output': str(target), 'sha256': checksum(target)}


def restore(snapshot, ledger, output):
    destination_ledger = d.deletion_ledger_path(Path(output).resolve())
    with staging(output) as (temp, target), staging(destination_ledger) as (ledger_temp, ledger_target):
        with contextlib.closing(read_database(snapshot)) as source, contextlib.closing(read_database(ledger)) as current_ledger:
            check(source)
            check(current_ledger)
            if identity(source) != identity(current_ledger):
                raise MaintenanceError('备份与删除记录不属于同一数据库实例')
            with contextlib.closing(sqlite3.connect(temp)) as recovered, contextlib.closing(sqlite3.connect(ledger_temp)) as replay:
                source.backup(recovered)
                current_ledger.backup(replay)
                replay.commit()
                recovered.row_factory = sqlite3.Row
                recovered.execute('PRAGMA foreign_keys=ON')
                recovered.execute('BEGIN IMMEDIATE')
                deleted = 0
                try:
                    for tombstone in replay.execute('SELECT user_id FROM tombstones ORDER BY deleted_at,user_id'):
                        user = tombstone[0]
                        if not recovered.execute('SELECT 1 FROM users WHERE id=?', (user,)).fetchone():
                            continue
                        if recovered.execute("SELECT 1 FROM memberships WHERE user_id=? AND role='dad' AND active=1", (user,)).fetchone():
                            raise MaintenanceError('备份中的旧统筹者已被删除，无法安全重放；请选择转交完成后的较新备份')
                        d.erase_account(recovered, user)
                        deleted += 1
                    # Restored sessions/grants/invites must not revive previous access.
                    recovered.execute('DELETE FROM sessions')
                    recovered.execute('DELETE FROM requests')
                    recovered.execute("UPDATE command_requests SET result='{}',invalidated=1")
                    for family in d.rows(recovered, 'family'):
                        fid = family['id']
                        d.cancel_transfers(recovered, fid)
                        for pending in d.rows(recovered, 'outbox', fid):
                            if pending['status'] == 'pending':
                                pending['status'] = 'cancelled'
                                d.put(recovered, 'outbox', pending, fid)
                        for member in recovered.execute('SELECT user_id FROM memberships WHERE family_id=?', (fid,)).fetchall():
                            d.cancel_member_approvals(recovered, fid, member[0])
                        for grant in d.rows(recovered, 'consent', fid):
                            if not grant.get('revoked_at'):
                                grant['revoked_at'] = d.now()
                                d.put(recovered, 'consent', grant, fid)
                        for invite in d.rows(recovered, 'invite', fid):
                            if not invite['used']:
                                invite['used'] = True
                                d.put(recovered, 'invite', invite, fid)
                        for notice in d.rows(recovered, 'notification', fid):
                            if not notice['read'] and not notice.get('cancelled'):
                                notice['cancelled'] = True
                                d.put(recovered, 'notification', notice, fid)
                    recovered.execute("INSERT OR REPLACE INTO metadata VALUES('restored_at',?)", (str(d.now()),))
                    recovered.commit()
                except Exception:
                    recovered.rollback()
                    raise
                check(recovered)
                check(replay)
        publish(ledger_temp, ledger_target)
        try:
            publish(temp, target)
        except Exception:
            ledger_target.unlink()
            raise
    return {'ok': True, 'operation': 'restore', 'output': str(target),
            'ledger': str(ledger_target), 'replayed_deletions': deleted, 'sha256': checksum(target)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    b = commands.add_parser('backup')
    b.add_argument('--db', required=True)
    b.add_argument('--output', required=True)
    r = commands.add_parser('restore')
    r.add_argument('--backup', required=True)
    r.add_argument('--ledger', required=True, help='当前最新的删除记录，不是旧快照中的副本')
    r.add_argument('--output', required=True)
    v = commands.add_parser('check')
    v.add_argument('--db', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'backup':
            result = backup(args.db, args.output)
        elif args.command == 'restore':
            result = restore(args.backup, args.ledger, args.output)
        else:
            with contextlib.closing(read_database(args.db)) as c:
                check(c)
            result = {'ok': True, 'operation': 'check'}
        print(json.dumps(result, ensure_ascii=False))
    except (MaintenanceError, sqlite3.Error, OSError, d.Problem) as exc:
        parser.exit(1, '操作未完成：' + (exc.message if isinstance(exc, d.Problem) else str(exc)) + '\n')


if __name__ == '__main__':
    main()
