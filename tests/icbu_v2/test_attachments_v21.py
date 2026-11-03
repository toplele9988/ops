"""旧事件别名、附件读回与掉盘保护，只使用虚构字节和临时目录。"""
import base64
from email.message import Message
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy' / 'icbu'))
from icbu_attachments import (AttachmentRejected, AttachmentStore, canonical_event_id,
                              event_alias, validate_fields)

BODY = b'fictional v2.1 attachment\n'
OLD_ID = 'icbu:v1:' + 'a' * 64
DIGEST = hashlib.sha256(BODY).hexdigest()


def metadata(event=OLD_ID, **changes):
    item = dict(attachment_id='fixture-file', kind='file', filename='fictional.txt',
                mime='text/plain', size=len(BODY), sha256=DIGEST)
    return dict(event_id=event, attachments=[dict(item, **changes)])


def headers():
    result = Message()
    for key, value in [('Content-Type', 'text/plain'), ('Content-Length', str(len(BODY))),
                       ('X-Content-SHA256', DIGEST)]:
        result[key] = value
    return result


@pytest.fixture
def store(tmp_path):
    return AttachmentStore(tmp_path / 'state' / 'attachments.sqlite3', tmp_path / 'files')


def query(store, statement):
    with sqlite3.connect(store.path) as db:
        return db.execute(statement).fetchall()


def rejected(code, action):
    with pytest.raises(AttachmentRejected) as failure:
        action()
    assert failure.value.code == code
    return failure.value.status


@pytest.mark.parametrize('original', [OLD_ID, 'event/with/path', 'event.with.period',
                                       'event@fixture', 'event+fixture', 'event=fixture'])
def test_legacy_ids_round_trip_without_mutating_the_canonical_id(original):
    alias = event_alias(original)
    assert alias == 'e1_' + base64.urlsafe_b64encode(original.encode()).decode().rstrip('=')
    assert canonical_event_id(alias, transport=True) == original
    assert canonical_event_id(original) == original
    assert len(alias) <= 127


def test_exact_93_byte_limit_and_native_safe_ids():
    original = ':' + 'a' * 92
    assert len(event_alias(original)) == 127
    assert canonical_event_id(event_alias(original), transport=True) == original
    assert event_alias('a' * 128) == 'a' * 128
    rejected('event_id_alias_too_long', lambda: event_alias(':' + 'a' * 93))
    rejected('event_id_alias_too_long', lambda: event_alias('a' * 129))


@pytest.mark.parametrize('value', ['', 'e1_', 'e1_!', 'e1_YQ', 'e1__w', 'e1_wyg',
                                    'e1_5Lit5paH', 'e1_Oh', 'e1_Og=', 'e1_' + 'A' * 125])
def test_aliases_must_decode_to_a_canonical_non_safe_legacy_id(value):
    code = 'invalid_event_alias' if value.startswith('e1_') else 'invalid_event_id'
    rejected(code, lambda: canonical_event_id(value, transport=True))


@pytest.mark.parametrize('value', ['e1_fixture', 'e1_Og', 'e1_', 'e1_x:y'])
def test_reserved_prefix_cannot_be_a_native_event_id(value):
    rejected('event_id_reserved_prefix', lambda: canonical_event_id(value))


def test_alias_must_not_smuggle_a_reserved_original_prefix():
    value = 'e1_' + base64.urlsafe_b64encode(b'e1_x:y').decode().rstrip('=')
    rejected('invalid_event_alias', lambda: canonical_event_id(value, transport=True))


def test_legacy_field_compatibility_is_explicit_and_has_no_payload_defaults():
    payload = metadata('event/with/path')
    rejected('invalid_event_id', lambda: validate_fields(payload))
    out = validate_fields(payload, allow_legacy_event_id=True)
    assert out == {'attachments': payload['attachments']}
    assert validate_fields({'content': '虚构文字'}, allow_legacy_event_id=True) == {}


def test_registered_then_uploaded_readback_has_one_canonical_binding(store):
    alias = event_alias(OLD_ID)
    missing = store.readback(alias, 'fixture-file')
    assert missing == dict(ok=True, persisted=False, registered=False, file_received=False,
                          size=None, sha256=None, kind=None, inbound_id=None, event_id=OLD_ID,
                          event_alias=alias, attachment_id='fixture-file', duplicate=False)
    store.link_messages('fictional-inbound', [metadata()], request_sha256='1' * 64)
    registered = store.readback(alias, 'fixture-file')
    assert registered['registered'] is True and registered['file_received'] is False
    assert registered['kind'] == 'file' and registered['sha256'] == DIGEST
    uploaded = store.receive_upload(alias, 'fixture-file', headers(), BytesIO(BODY))
    assert uploaded['duplicate'] is False and uploaded['inbound_id'] == 'fictional-inbound'
    assert uploaded['event_id'] == OLD_ID and uploaded['event_alias'] == alias
    assert uploaded['kind'] == 'file'
    result = store.readback(alias, 'fixture-file')
    assert result == store.readback(OLD_ID, 'fixture-file', transport=False)
    assert result['registered'] and result['file_received'] and result['persisted']
    assert result['size'] == len(BODY) and result['duplicate'] is True
    assert result['inbound_id'] == 'fictional-inbound'
    assert not {'content', 'filename', 'path', 'bytes'} & result.keys()
    assert query(store, 'SELECT event_id,attachment_id FROM attachments') == [(OLD_ID, 'fixture-file')]


def test_upload_duplicate_does_not_prove_message_link_and_late_link_repairs(store):
    alias = event_alias(OLD_ID)
    store.receive_upload(alias, 'fixture-file', headers(), BytesIO(BODY))
    reply = store.receive_upload(alias, 'fixture-file', headers(), BytesIO(BODY))
    assert reply['duplicate'] is True and reply['inbound_id'] is None and reply['kind'] is None
    before = store.readback(alias, 'fixture-file')
    assert before['file_received'] and before['inbound_id'] is None
    store.link_messages('fictional-inbound', [metadata()], request_sha256='2' * 64)
    after = store.readback(alias, 'fixture-file')
    assert after['file_received'] and after['inbound_id'] == 'fictional-inbound'
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(1,)]


def test_readback_detects_missing_or_corrupt_content(store):
    alias = event_alias(OLD_ID)
    store.receive_upload(alias, 'fixture-file', headers(), BytesIO(BODY))
    blob = store.root / (DIGEST + '.bin')
    blob.unlink()
    assert store.readback(alias, 'fixture-file')['file_received'] is False
    blob.write_bytes(b'x' * len(BODY))
    blob.chmod(0o600)
    rejected('attachment_blob_conflict', lambda: store.readback(alias, 'fixture-file'))


def test_another_events_cached_bytes_do_not_count_as_received_for_a_declaration(store):
    store.receive_upload('fictional-first-event', 'fixture-file', headers(), BytesIO(BODY))
    store.link_messages('fictional-inbound', [metadata()], request_sha256='6' * 64)
    reply = store.readback(event_alias(OLD_ID), 'fixture-file')
    assert reply['registered'] and reply['sha256'] == DIGEST
    assert reply['file_received'] is False and reply['duplicate'] is False
    uploaded = store.receive_upload(event_alias(OLD_ID), 'fixture-file', headers(), BytesIO(BODY))
    assert uploaded['duplicate'] is True
    assert store.readback(event_alias(OLD_ID), 'fixture-file')['file_received'] is True


def test_ledger_is_idempotent_and_retains_original_id_alias_and_request_hash(store):
    for digest in ['3' * 64, '3' * 64, '4' * 64]:
        store.link_messages('fictional-inbound', [metadata()], request_sha256=digest)
    assert query(store, '''SELECT event_id,event_alias,inbound_id,request_sha256
                 FROM enrichment_ledger ORDER BY request_sha256''') == [
        (OLD_ID, event_alias(OLD_ID), 'fictional-inbound', '3' * 64),
        (OLD_ID, event_alias(OLD_ID), 'fictional-inbound', '4' * 64)]
    assert query(store, 'PRAGMA user_version') == [(1,)]


def test_failed_enrichment_rolls_back_ledger_with_binding(store, monkeypatch):
    def unavailable(*args):
        raise AttachmentRejected('attachment_storage_unavailable', 503)
    monkeypatch.setattr(store, '_mark', unavailable)
    rejected('attachment_storage_unavailable', lambda: store.link_messages(
        'fictional-inbound', [metadata()], request_sha256='5' * 64))
    assert query(store, 'SELECT COUNT(*) FROM enrichment_ledger') == [(0,)]
    assert query(store, 'SELECT COUNT(*) FROM message_links') == [(0,)]
    monkeypatch.undo()
    store.link_messages('fictional-inbound', [metadata()], request_sha256='5' * 64)
    assert query(store, 'SELECT COUNT(*) FROM enrichment_ledger') == [(1,)]


def mounted_store(tmp_path):
    archive = tmp_path / 'fictional-archive'
    root = archive / 'icbu' / 'attachments'
    root.mkdir(parents=True, mode=0o700)
    marker = archive / '.imgw-archive-id'
    marker.write_text('fictional sentinel, no real archive identity')
    marker.chmod(0o600)
    return AttachmentStore(tmp_path / 'state' / 'attachments.sqlite3', root, archive_root=archive), marker


def test_nas_startup_missing_sentinel_never_creates_local_fallback(tmp_path):
    archive = tmp_path / 'absent-archive'
    root = archive / 'icbu' / 'attachments'
    assert rejected('attachment_storage_unavailable', lambda: AttachmentStore(
        tmp_path / 'state' / 'attachments.sqlite3', root, archive_root=archive)) == 503
    assert not archive.exists() and not (tmp_path / 'state').exists()


def test_nas_startup_with_sentinel_still_requires_preexisting_attachment_root(tmp_path):
    archive = tmp_path / 'fictional-archive'
    archive.mkdir()
    marker = archive / '.imgw-archive-id'
    marker.touch(mode=0o600)
    root = archive / 'icbu' / 'attachments'
    rejected('attachment_storage_unavailable', lambda: AttachmentStore(
        tmp_path / 'state' / 'attachments.sqlite3', root, archive_root=archive))
    assert not root.parent.exists()


def test_nas_missing_mount_during_runtime_returns_503_and_never_recreates_root(tmp_path):
    store, marker = mounted_store(tmp_path)
    archive = marker.parent
    archive.rename(tmp_path / 'detached-archive')
    rejected('attachment_storage_unavailable', lambda: store.receive_upload(
        event_alias(OLD_ID), 'fixture-file', headers(), BytesIO(BODY)))
    assert not archive.exists()
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]


def test_nas_sentinel_disappears_mid_upload_and_cannot_ack_old_descriptor(tmp_path):
    store, marker = mounted_store(tmp_path)
    class LostMount(BytesIO):
        def read(self, count):
            marker.unlink()
            return super().read(count)
        read1 = read
    assert rejected('attachment_storage_unavailable', lambda: store.receive_upload(
        event_alias(OLD_ID), 'fixture-file', headers(), LostMount(BODY))) == 503
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]
    assert list(store.root.glob('*.bin')) == []


def test_nas_replaced_root_identity_fails_before_creating_lock(tmp_path):
    store, _ = mounted_store(tmp_path)
    store.root.rename(store.root.with_name('detached-attachments'))
    store.root.mkdir(mode=0o700)
    rejected('attachment_storage_unavailable', lambda: store.mark_orphans())
    assert list(store.root.iterdir()) == []


def test_kind_cannot_be_changed_by_a_later_event_alias(store):
    initial = metadata('fixture-first', kind='image', mime='image/png', filename='fictional.png')
    store.link_message('fictional-inbound', initial)
    replacement = metadata('fixture-second', kind='file', mime='image/png', filename='fictional.png')
    rejected('attachment_metadata_conflict', lambda: store.link_message('fictional-inbound', replacement))
    assert query(store, 'SELECT kind FROM attachment_kinds') == [('image',)]


def test_batch_kind_conflict_rolls_back_every_alias_and_ledger_entry(store):
    first = metadata('fictional-first', kind='image', mime='image/png', filename='fictional.png')
    second = metadata('fictional-second', kind='file', mime='image/png', filename='fictional.png')
    rejected('attachment_metadata_conflict', lambda: store.link_messages('fictional-inbound', [first, second]))
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]
    assert query(store, 'SELECT COUNT(*) FROM enrichment_ledger') == [(0,)]
