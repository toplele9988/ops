"""Only synthetic authorization values and temporary local files."""
from concurrent.futures import ThreadPoolExecutor
import json
import os

import pytest

from console.archive import validation as v
from console.alibaba_open.store import Store

NOW = 2_000_000_000


@pytest.fixture
def private_store(tmp_path):
    return Store(tmp_path / 'orders.sqlite3', clock=lambda: NOW)


def test_status_and_empty_read_do_not_create_files(private_store):
    assert private_store.metadata() == dict(has_token=False, expires_at=None,
                                          refresh_expires_at=None, reauthorize=False)
    assert private_store.token() is None
    assert not private_store.path.exists()
    assert not private_store.key_path.exists()
    assert not list(private_store.path.parent.iterdir())


def test_token_is_encrypted_with_private_key_and_value_free_audit(private_store, caplog):
    secret = 'SYNTHETIC-TOKEN-SENTINEL'
    private_store.save_token(dict(access_token=secret, refresh_token='SYNTHETIC-REFRESH-SENTINEL',
                                 expires_in=3600, refresh_expires_in=7200), 'fictional-owner')
    assert private_store.token()['access_token'] == secret
    assert private_store.metadata()['expires_at'] == NOW + 3600
    assert private_store.metadata()['refresh_expires_at'] == NOW + 7200
    assert os.stat(private_store.path).st_mode & 0o777 == 0o600
    assert os.stat(private_store.key_path).st_mode & 0o777 == 0o600
    assert secret.encode() not in private_store.path.read_bytes()
    assert secret not in caplog.text
    with private_store.read() as db:
        audit = [dict(r) for r in db.execute('SELECT * FROM audit')]
    assert audit[0]['operation'] == 'token_saved'
    assert secret not in json.dumps(audit)


def test_save_without_refresh_token_allows_nonrefreshable_authorization(private_store):
    private_store.save_token(dict(access_token='synthetic', refresh_token='',
                                 expires_in=3600, refresh_expires_in=0))
    assert private_store.token()['refresh_token'] == ''
    assert private_store.metadata()['refresh_expires_at'] == NOW


def test_status_does_not_read_encrypted_values_or_key(private_store, monkeypatch):
    private_store.save_token(dict(access_token='synthetic', refresh_token='synthetic-refresh', expires_in=3600))
    monkeypatch.setattr(private_store, '_cipher', lambda **kwargs: pytest.fail('状态不应读取密钥'))
    assert private_store.metadata()['has_token']


def test_missing_key_and_invalid_ciphertext_return_fixed_errors(private_store):
    private_store.save_token(dict(access_token='synthetic', refresh_token='synthetic-refresh', expires_in=3600))
    private_store.key_path.unlink()
    with pytest.raises(v.BadRequest, match='密钥缺失'):
        private_store.token()
    with private_store.write() as db:
        db.execute('UPDATE token_state SET encrypted=?', (b'synthetic-corrupt',))
    private_store._cipher(create=True)
    with pytest.raises(v.BadRequest, match='无法解密'):
        private_store.token()


@pytest.mark.parametrize('kind', ['db', 'key', 'sidecar', 'parent'])
def test_storage_rejects_symlinks(tmp_path, kind):
    target = tmp_path / 'target'
    target.mkdir()
    store = Store(tmp_path / 'orders.sqlite3', clock=lambda: NOW)
    if kind == 'parent':
        (tmp_path / 'link').symlink_to(target, target_is_directory=True)
        store = Store(tmp_path / 'link' / 'orders.sqlite3', clock=lambda: NOW)
    else:
        path = store.path if kind == 'db' else store.key_path if kind == 'key' else tmp_path / 'orders.sqlite3-wal'
        path.symlink_to(target / 'absent')
    with pytest.raises(v.BadRequest, match='路径不可用'):
        store.save_token(dict(access_token='synthetic', refresh_token='synthetic-refresh', expires_in=3600))


def test_insecure_permissions_rejected(private_store):
    private_store.path.touch(mode=0o644)
    private_store.path.chmod(0o644)
    with pytest.raises(v.BadRequest, match='权限不正确'):
        private_store.metadata()


def test_failed_write_rolls_back_and_writers_serialize(private_store):
    with pytest.raises(RuntimeError):
        with private_store.write() as db:
            db.execute("INSERT INTO kv VALUES('rollback','1')")
            raise RuntimeError('synthetic failure')
    with private_store.read() as db:
        assert not db.execute('SELECT * FROM kv').fetchall()
    with private_store.write() as db:
        db.execute("INSERT INTO kv VALUES('counter','0')")
    def increment(_):
        with private_store.write() as db:
            value = int(db.execute("SELECT value FROM kv WHERE key='counter'").fetchone()[0])
            db.execute("UPDATE kv SET value=? WHERE key='counter'", (str(value + 1),))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment, range(16)))
    with private_store.read() as db:
        assert db.execute("SELECT value FROM kv WHERE key='counter'").fetchone()[0] == '16'


@pytest.mark.parametrize('invalid', [dict(access_token='', refresh_token='synthetic', expires_in=3600),
                                       dict(access_token='synthetic', refresh_token='synthetic', expires_in=-1),
                                       dict(access_token='synthetic', refresh_token='synthetic', expires_at=float('nan'))])
def test_invalid_authorization_never_persisted(private_store, invalid):
    with pytest.raises(v.BadRequest, match='授权返回'):
        private_store.save_token(invalid)
    assert not private_store.path.exists()


def test_reauthorization_flag_does_not_decrypt(private_store):
    private_store.save_token(dict(access_token='synthetic', refresh_token='synthetic-refresh', expires_in=3600))
    private_store.mark_reauthorize()
    assert private_store.metadata()['reauthorize']
