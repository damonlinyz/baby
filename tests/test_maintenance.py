import os
from pathlib import Path
import sqlite3
import unittest
import test_domain
import domain as d
import maintenance as m


class MaintenanceTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def path(self,name):return Path(self.tmp.name)/name
 def snapshot(self):
  path=self.path('backup.sqlite3');m.backup(d.DB,path);return path
 def connect(self,path):
  c=sqlite3.connect(path);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');self.addCleanup(c.close);return c

 def test_backup_is_valid_private_and_never_overwrites(self):
  output=self.snapshot();original=m.checksum(output)
  self.assertEqual(os.stat(output).st_mode&0o777,0o600)
  m.check(self.connect(output))
  with self.assertRaises(m.MaintenanceError):m.backup(d.DB,output)
  self.assertEqual(m.checksum(output),original)
  self.assertEqual(self.connect(output).execute('SELECT count(*) FROM users').fetchone()[0],3)

 def test_recovery_replays_later_deletion_and_preserves_tombstones(self):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  with d.transaction() as c:d.new_session(c,self.dad)
  snapshot=self.snapshot()
  with d.transaction() as c:d.delete_account(c,self.mom,'long-password-123')
  output=self.path('restored.sqlite3');result=m.restore(snapshot,d.deletion_ledger_path(),output)
  self.assertEqual(result['replayed_deletions'],1)
  c=self.connect(output);m.check(c)
  self.assertFalse(c.execute('SELECT 1 FROM users WHERE id=?',(self.mom,)).fetchone())
  self.assertEqual(d.rows(c,'pregnancy',self.fid),[])
  self.assertIsNone(d.snapshot(c,self.dad,self.fid)['pregnancy'])
  self.assertEqual(c.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
  self.assertEqual(self.connect(d.deletion_ledger_path(output)).execute('SELECT user_id FROM tombstones').fetchone()[0],self.mom)

 def test_recovery_revokes_sessions_grants_invites_and_pending_transfers(self):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  self.call(self.mom,'consent.update',{'grantee':self.dad,'scope':'health_read','enabled':True})
  inv=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,inv['invite_code']);d.new_session(c,self.dad)
  self.call(self.dad,'family.transfer',{'user_id':self.other})
  self.call(self.dad,'invite.create',{'role':'member'})
  snapshot=self.snapshot();output=self.path('restored.sqlite3');m.restore(snapshot,d.deletion_ledger_path(),output)
  c=self.connect(output)
  self.assertTrue(d.snapshot(c,self.dad,self.fid)['pregnancy']['restricted'])
  self.assertEqual(c.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
  self.assertTrue(all(g['revoked_at'] for g in d.rows(c,'consent',self.fid)))
  self.assertTrue(all(i['used'] for i in d.rows(c,'invite',self.fid)))
  self.assertTrue(all(t['status']=='cancelled' for t in d.rows(c,'transfer',self.fid)))

 def test_delete_and_tombstone_are_rolled_back_together(self):
  with self.assertRaises(RuntimeError):
   with d.transaction() as c:d.delete_account(c,self.mom,'long-password-123');raise RuntimeError('合成事务失败')
  self.assertTrue(self.connect(d.DB).execute('SELECT 1 FROM users WHERE id=?',(self.mom,)).fetchone())
  self.assertEqual(self.connect(d.deletion_ledger_path()).execute('SELECT count(*) FROM tombstones').fetchone()[0],0)

 def test_wrong_ledger_refuses_to_publish_restored_files(self):
  snapshot=self.snapshot();wrong=self.path('wrong.sqlite3');c=self.connect(wrong)
  c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)');c.execute("INSERT INTO metadata VALUES('instance_id','different')");c.commit()
  output=self.path('restored.sqlite3')
  with self.assertRaises(m.MaintenanceError):m.restore(snapshot,wrong,output)
  self.assertFalse(output.exists());self.assertFalse(d.deletion_ledger_path(output).exists())

 def test_missing_ledger_and_existing_output_are_rejected(self):
  snapshot=self.snapshot();output=self.path('restored.sqlite3')
  with self.assertRaises(m.MaintenanceError):m.restore(snapshot,self.path('missing.sqlite3'),output)
  self.assertFalse(output.exists())
  output.write_bytes(b'existing')
  with self.assertRaises(m.MaintenanceError):m.restore(snapshot,d.deletion_ledger_path(),output)
  self.assertEqual(output.read_bytes(),b'existing')

 def test_old_snapshot_of_deleted_coordinator_is_rejected(self):
  inv=self.call(self.dad,'invite.create',{'role':'member'})
  with d.transaction() as c:d.join(c,self.other,inv['invite_code'])
  snapshot=self.snapshot();tid=self.call(self.dad,'family.transfer',{'user_id':self.other})['transfer_id']
  with d.transaction() as c:t=d.get(c,'transfer',tid)
  self.call(self.other,'transfer.accept',{'id':tid,'version':t['version']})
  with d.transaction() as c:d.delete_account(c,self.dad,'long-password-123')
  output=self.path('restored.sqlite3')
  with self.assertRaises(m.MaintenanceError) as err:m.restore(snapshot,d.deletion_ledger_path(),output)
  self.assertIn('较新备份',str(err.exception));self.assertFalse(output.exists())

 def test_corrupt_database_does_not_produce_backup(self):
  bad=self.path('bad.sqlite3');bad.write_bytes(b'not a database');output=self.path('out.sqlite3')
  with self.assertRaises(sqlite3.DatabaseError):m.backup(bad,output)
  self.assertFalse(output.exists())
