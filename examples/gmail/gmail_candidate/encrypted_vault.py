"""AES-256-GCM vault candidate. External key provider is mandatory.

No key acquisition or key persistence mechanism is supplied. This file is not
production-ready until a trusted key provider and access boundary are audited.
"""
from __future__ import annotations
import ctypes,errno,hashlib,hmac,json,os,secrets,stat,sys
from pathlib import Path
from gmail_sync import canonical_account

MAGIC=b'LECHU-GMAIL-ENC-v1\x00'
MAX_BYTES=65536

class VaultError(Exception):pass


def directory(path):
    path=Path(path);s=path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o700:
        raise VaultError('unsafe_vault_directory')
    return path


def read_file(path):
    path=Path(path);directory(path.parent)
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except OSError:raise VaultError('vault_file_unavailable') from None
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_nlink!=1 or stat.S_IMODE(s.st_mode)!=0o600 or s.st_size>MAX_BYTES:
            raise VaultError('unsafe_vault_file')
        raw=os.read(fd,MAX_BYTES+1)
    finally:os.close(fd)
    if len(raw)>MAX_BYTES:raise VaultError('vault_file_too_large')
    return raw


def write_file(path,raw):
    path=Path(path);directory(path.parent)
    if not isinstance(raw,bytes) or len(raw)>MAX_BYTES:raise VaultError('invalid_vault_data')
    try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except OSError:raise VaultError('vault_file_exists_or_unavailable') from None
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(os.dup(fd),'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    except BaseException:
        try:os.unlink(path)
        except OSError:pass
        raise
    finally:os.close(fd)


def atomic_publish(pending,target):
    # Atomic no-replace rename; never publish via hard link because a crash
    # between link and unlink would leave a second link to the active file.
    libc=ctypes.CDLL(None,use_errno=True)
    a=os.fsencode(pending);b=os.fsencode(target)
    if sys.platform.startswith('linux') and hasattr(libc,'renameat2'):
        fn=libc.renameat2;fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
        rc=fn(-100,a,-100,b,1) # AT_FDCWD, RENAME_NOREPLACE
    elif sys.platform=='darwin' and hasattr(libc,'renamex_np'):
        fn=libc.renamex_np;fn.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
        rc=fn(a,b,4) # RENAME_EXCL
    else:raise VaultError('atomic_noreplace_unavailable')
    if rc:
        number=ctypes.get_errno()
        if number==errno.EEXIST:raise VaultError('vault_file_exists_or_unavailable')
        raise VaultError('atomic_publish_failed')

def _key(provider):
    if not callable(provider):raise VaultError('key_provider_unavailable')
    try:key=provider()
    except Exception:key=None
    if not isinstance(key,bytes) or len(key)!=32:
        key=None
        raise VaultError('key_provider_unavailable')
    return key


def _aes(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:raise VaultError('aesgcm_unavailable') from None
    return AESGCM(key)


def _binding(account):return hashlib.sha256(canonical_account(account).encode()).digest()


def seal(payload,account,key_provider):
    if not isinstance(payload,dict) or payload.get('account')!=canonical_account(account):raise VaultError('account_binding_invalid')
    try:plain=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    except (ValueError,TypeError):raise VaultError('invalid_vault_data') from None
    if len(plain)>MAX_BYTES-128:raise VaultError('vault_file_too_large')
    binding=_binding(account);nonce=secrets.token_bytes(12);aad=MAGIC+binding
    key=_key(key_provider)
    try:encrypted=_aes(key).encrypt(nonce,plain,aad)
    finally:key=None;plain=None
    return aad+nonce+encrypted


def unseal(blob,account,key_provider):
    if not isinstance(blob,bytes) or len(blob)<len(MAGIC)+32+12+16 or len(blob)>MAX_BYTES or not blob.startswith(MAGIC):raise VaultError('invalid_vault_data')
    n=len(MAGIC);binding=blob[n:n+32]
    if not hmac.compare_digest(binding,_binding(account)):raise VaultError('account_binding_invalid')
    nonce=blob[n+32:n+44];cipher=blob[n+44:];key=_key(key_provider);plain=None
    try:
        try:plain=_aes(key).decrypt(nonce,cipher,MAGIC+binding)
        except Exception:plain=None
    finally:key=None
    if plain is None:raise VaultError('vault_authentication_failed')
    try:payload=json.loads(plain.decode('utf-8','strict'))
    except (ValueError,UnicodeError):payload=None
    plain=None
    if not isinstance(payload,dict) or payload.get('account')!=canonical_account(account):raise VaultError('invalid_vault_data')
    return payload


class EncryptedVault:
    def __init__(self,path,key_provider):self.path=Path(path);self.key_provider=key_provider
    def create(self,account,payload):
        raw=seal(payload,account,self.key_provider)
        pending=self.path.parent/(self.path.name+'.pending-'+secrets.token_hex(12))
        write_file(pending,raw)
        try:
            atomic_publish(pending,self.path)
            dfd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            try:pending.unlink()
            except FileNotFoundError:pass
    def load(self,account):return unseal(read_file(self.path),account,self.key_provider)
    def replace(self,account,old,new):
        # Caller must hold the same credential lock for all writers.
        raw=read_file(self.path)
        if unseal(raw,account,self.key_provider)!=old:raise VaultError('vault_changed')
        updated=seal(new,account,self.key_provider)
        suffix=secrets.token_hex(12);backup=self.path.parent/(self.path.name+'.backup-'+suffix);pending=self.path.parent/(self.path.name+'.pending-'+suffix)
        write_file(backup,raw)
        # Make the backup directory entry durable before replacing the only
        # active copy; an interrupted replace must leave a usable backup.
        dfd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
        try:
            write_file(pending,updated)
            if read_file(self.path)!=raw:raise VaultError('vault_changed')
            os.replace(pending,self.path)
            dfd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            try:pending.unlink()
            except FileNotFoundError:pass
