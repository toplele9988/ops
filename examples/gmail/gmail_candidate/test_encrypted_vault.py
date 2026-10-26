import os,stat,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from encrypted_vault import EncryptedVault,VaultError,read_file,seal,unseal

class VaultTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'test.enc';self.account='demo@example.test';self.key=b'K'*32
  self.vault=EncryptedVault(self.path,lambda:self.key);self.data={'account':self.account,'refresh_token':'SYNTHETIC-REFRESH','client_secret':'SYNTHETIC-CLIENT'}
 def tearDown(self):self.tmp.cleanup()
 def test_encryption_random_nonce_permissions_and_account_binding(self):
  a=seal(self.data,self.account,lambda:self.key);b=seal(self.data,self.account,lambda:self.key)
  self.assertNotEqual(a,b);self.assertNotIn(b'SYNTHETIC-REFRESH',a);self.assertNotIn(b'SYNTHETIC-CLIENT',a)
  self.vault.create(self.account,self.data);self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600);self.assertEqual(self.vault.load(self.account),self.data)
  with self.assertRaisesRegex(VaultError,'account_binding_invalid'):self.vault.load('other@example.test')
 def test_missing_wrong_and_raising_key_provider_fail_closed(self):
  for provider in (None,lambda:None,lambda:b'short',lambda:(_ for _ in ()).throw(ValueError('secret-not-to-log'))):
   with self.assertRaisesRegex(VaultError,'key_provider_unavailable'):seal(self.data,self.account,provider)
  self.assertFalse(self.path.exists());self.vault.create(self.account,self.data)
  with self.assertRaisesRegex(VaultError,'vault_authentication_failed'):EncryptedVault(self.path,lambda:b'X'*32).load(self.account)
 def test_tampered_ciphertext_and_plaintext_rejected(self):
  self.vault.create(self.account,self.data);raw=bytearray(self.path.read_bytes());raw[-1]^=1;self.path.write_bytes(raw)
  with self.assertRaisesRegex(VaultError,'vault_authentication_failed'):self.vault.load(self.account)
  self.path.write_bytes(b'{"refresh_token":"plain"}')
  with self.assertRaisesRegex(VaultError,'invalid_vault_data'):self.vault.load(self.account)
 def test_encrypted_backup_and_compare_swap(self):
  self.vault.create(self.account,self.data);new=dict(self.data,refresh_token='SYNTHETIC-ROTATED')
  self.vault.replace(self.account,self.data,new);self.assertEqual(self.vault.load(self.account),new)
  backup=list(self.root.glob('test.enc.backup-*'));self.assertEqual(len(backup),1);self.assertEqual(stat.S_IMODE(backup[0].stat().st_mode),0o600)
  self.assertNotIn(b'SYNTHETIC-REFRESH',backup[0].read_bytes());self.assertEqual(unseal(read_file(backup[0]),self.account,lambda:self.key),self.data)
  before=self.path.read_bytes()
  with self.assertRaisesRegex(VaultError,'vault_changed'):self.vault.replace(self.account,self.data,new)
  self.assertEqual(before,self.path.read_bytes())
 def test_unsafe_directory_file_and_reuse_rejected(self):
  os.chmod(self.root,0o755)
  with self.assertRaisesRegex(VaultError,'unsafe_vault_directory'):self.vault.create(self.account,self.data)
  os.chmod(self.root,0o700);self.vault.create(self.account,self.data)
  with self.assertRaisesRegex(VaultError,'vault_file_exists'):self.vault.create(self.account,self.data)
  os.chmod(self.path,0o644)
  with self.assertRaisesRegex(VaultError,'unsafe_vault_file'):self.vault.load(self.account)
 def test_crash_before_publication_leaves_no_final_and_retry_succeeds(self):
  import encrypted_vault as m
  original=m.write_file
  pid=os.fork()
  if pid==0:
   def interrupted(path,raw):
    original(path,raw);os._exit(73)
   m.write_file=interrupted
   self.vault.create(self.account,self.data);os._exit(99)
  _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),73)
  self.assertFalse(self.path.exists())
  self.vault.create(self.account,self.data);self.assertEqual(self.vault.load(self.account),self.data)
  self.assertEqual(self.path.stat().st_nlink,1)
 def test_backup_directory_fsync_precedes_active_replace(self):
  import encrypted_vault as m
  self.vault.create(self.account,self.data);events=[]
  original_fsync=m.os.fsync;original_replace=m.os.replace
  def fsync(fd):
   if stat.S_ISDIR(os.fstat(fd).st_mode):events.append('dirsync')
   return original_fsync(fd)
  def replace(src,dst):
   events.append('replace');return original_replace(src,dst)
  with patch.object(m.os,'fsync',fsync),patch.object(m.os,'replace',replace):
   self.vault.replace(self.account,self.data,dict(self.data,refresh_token='new'))
  self.assertLess(events.index('dirsync'),events.index('replace'))
 def test_cli_fails_closed_without_key_provider(self):
  script=Path(__file__).with_name('gmail_oauth.py')
  for action in ('authorize','sync','retry'):
   p=subprocess.run([sys.executable,str(script),action,'--private-dir',str(self.root),'--expected-account',self.account],capture_output=True,text=True)
   self.assertNotEqual(p.returncode,0);self.assertIn('encrypted_vault_not_configured',p.stderr);self.assertFalse(self.path.exists());self.assertNotIn('https://accounts.google.com',p.stdout)
if __name__=='__main__':unittest.main()
