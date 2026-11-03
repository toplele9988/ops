"""只用虚构载荷与临时目录，覆盖附件归档与 v2 字段边界。"""
from email.message import Message
import errno
import hashlib
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy' / 'icbu'))
import icbu_attachments as module
from icbu_attachments import AttachmentRejected, AttachmentStore, MAX_FILE_BYTES, validate_fields

BODY = b'Fictional attachment used only by isolated tests.\n'


def attachment(**changes):
    return dict(attachment_id='spec-1', kind='file', filename='fictional-spec.txt', mime='text/plain',
                size=len(BODY), sha256=hashlib.sha256(BODY).hexdigest(), **changes) if not changes else {
                    **attachment(), **changes}


def payload(**changes):
    return {'event_id': 'fictional-event', 'attachments': [attachment()], **changes}


def headers(body=BODY, **changes):
    values = {'Content-Length': str(len(body)), 'Content-Type': 'text/plain',
              'X-Content-SHA256': hashlib.sha256(body).hexdigest(), **changes}
    result = Message()
    for key, value in values.items():
        if value is not None:
            result[key] = value
    return result


@pytest.fixture
def store(tmp_path):
    return AttachmentStore(tmp_path / 'db' / 'attachments.sqlite3', tmp_path / 'files')


def query(store, statement, args=()):
    with sqlite3.connect(store.path) as db:
        return db.execute(statement, args).fetchall()


def rejected(code, func):
    with pytest.raises(AttachmentRejected) as exc:
        func()
    assert exc.value.code == code
    return exc.value.status


def test_legacy_has_no_new_defaults_and_card_urls_do_not_retain_queries():
    assert validate_fields({'content': '虚构文字'}) == {}
    out = validate_fields({'event_id': 'event', 'cards': [dict(card_type='product', title='虚构商品',
             url='https://example.invalid/product?discard=yes#discard', product_id='fixture-product',
             image_attachment_id='picture-1', price_text='仅虚构示例', moq_text='示例起订量', subtitle='虚构说明')]})
    assert out['cards'][0]['url'] == 'https://example.invalid/product'
    assert 'event_id' not in out


@pytest.mark.parametrize('change,code', [
    ({'message_kind': 'unsupported'}, 'invalid_message_kind'),
    ({'message_kind': []}, 'invalid_message_kind'),
    ({'attachments': {}}, 'invalid_attachments'),
    ({'attachments': [attachment()] * 21}, 'invalid_attachments'),
    ({'attachments': [attachment(), attachment()]}, 'duplicate_attachment_id'),
    ({'attachments': [{}]}, 'invalid_attachment_fields'),
    ({'attachments': [attachment(extra='unknown')]}, 'invalid_attachment_fields'),
    ({'attachments': [attachment(attachment_id='../traversal')]}, 'invalid_attachment_id'),
    ({'attachments': [attachment(attachment_id='a' * 129)]}, 'invalid_attachment_id'),
    ({'attachments': [attachment(kind=[])]}, 'invalid_attachment_kind'),
    ({'attachments': [attachment(kind='script')]}, 'invalid_attachment_kind'),
    ({'attachments': [attachment(kind='image')]}, 'attachment_kind_mime_mismatch'),
    ({'attachments': [attachment(mime='image/svg+xml')]}, 'invalid_attachment_mime'),
    ({'attachments': [attachment(size=True)]}, 'invalid_attachment_size'),
    ({'attachments': [attachment(size=0)]}, 'invalid_attachment_size'),
    ({'attachments': [attachment(size=MAX_FILE_BYTES + 1)]}, 'invalid_attachment_size'),
    ({'attachments': [attachment(sha256='A' * 64)]}, 'invalid_attachment_sha256'),
    ({'cards': {}}, 'invalid_cards'),
    ({'cards': [dict(card_type='link', title='虚构卡片')] * 11}, 'invalid_cards'),
    ({'cards': [dict(card_type='unknown', title='虚构卡片')]}, 'invalid_card_type'),
    ({'cards': [dict(card_type=[], title='虚构卡片')]}, 'invalid_card_type'),
    ({'cards': [dict(card_type='link', title='x' * 301)]}, 'invalid_card_title'),
    ({'cards': [dict(card_type='link', title='x', subtitle='x' * 1001)]}, 'invalid_card_subtitle'),
    ({'cards': [dict(card_type='link', title='x', price_text=12)]}, 'invalid_card_price_text'),
    ({'cards': [dict(card_type='link', title='x', product_id='x' * 129)]}, 'invalid_card_product_id'),
    ({'cards': [dict(card_type='link', title='x', moq_text='x' * 201)]}, 'invalid_card_moq_text'),
    ({'cards': [dict(card_type='link', title='x', image_attachment_id='../x')]}, 'invalid_card_image_attachment_id'),
    ({'cards': [dict(card_type='link', title='x', extra='unknown')]}, 'invalid_card_fields'),
    ({'event_id': None}, 'invalid_event_id'),
    ({'event_id': 'event/with/path'}, 'invalid_event_id'),
])
def test_field_boundaries_reject_clear_codes(change, code):
    rejected(code, lambda: validate_fields(payload(**change)))
    assert any('\u4e00' <= c <= '\u9fff' for c in module.rejection_message(code))


@pytest.mark.parametrize('filename', ['../a.txt', '/a.txt', r'c:\a.txt', 'a.exe.txt', 'a.html.txt',
                                    'a.svg', 'a.txt:stream', 'a%2ftxt.txt', '.hidden.txt', 'a..txt',
                                    'a.txt ', ' a.txt', 'a\u202etxt.txt', 'a\x00.txt', 'CON.txt', 'x' * 201])
def test_suspicious_filenames_rejected(filename):
    rejected('invalid_attachment_filename', lambda: validate_fields(payload(attachments=[attachment(filename=filename)])))


@pytest.mark.parametrize('url', ['http://example.invalid/a', 'javascript:alert(1)', 'https://user@example.invalid/a',
                               'https://example.invalid:8443/a', 'https://example.invalid/ has-space',
                               r'https://example.invalid\evil/a', 'https://', 'https://例.invalid/a'])
def test_bad_card_urls_rejected(url):
    rejected('invalid_card_url', lambda: validate_fields(payload(cards=[dict(card_type='link', title='虚构卡片', url=url)])))


def test_exact_field_limits_accepted():
    values = [attachment(attachment_id=f'file-{i}') for i in range(20)]
    cards = [dict(card_type='inquiry', title='t' * 300, subtitle='s' * 1000,
                  product_id='p' * 127 + str(i), price_text='p' * 200, moq_text='m' * 200) for i in range(10)]
    assert len(validate_fields(payload(attachments=values, cards=cards))['attachments']) == 20
    assert validate_fields(payload(attachments=[attachment(size=MAX_FILE_BYTES)]))['attachments'][0]['size'] == MAX_FILE_BYTES
    assert validate_fields(payload(attachments=[attachment(filename='虚构资料.txt')]))['attachments'][0]['filename'] == '虚构资料.txt'


@pytest.mark.parametrize('changes,code,status', [
    ({'Content-Length': None}, 'attachment_content_length_required', 411),
    ({'Content-Length': '-1'}, 'attachment_content_length_required', 411),
    ({'Content-Length': '0'}, 'attachment_body_too_large', 413),
    ({'Content-Length': str(MAX_FILE_BYTES + 1)}, 'attachment_body_too_large', 413),
    ({'Content-Type': 'image/svg+xml'}, 'attachment_content_type_not_supported', 415),
    ({'Content-Type': 'text/html'}, 'attachment_content_type_not_supported', 415),
    ({'Content-Type': 'application/javascript'}, 'attachment_content_type_not_supported', 415),
    ({'Content-Type': 'text/plain; charset=utf-8'}, 'attachment_content_type_not_supported', 415),
    ({'X-Content-SHA256': None}, 'attachment_sha256_required', 400),
    ({'X-Content-SHA256': 'A' * 64}, 'attachment_sha256_required', 400),
    ({'Transfer-Encoding': 'chunked'}, 'attachment_encoding_not_supported', 400),
    ({'Content-Encoding': 'gzip'}, 'attachment_encoding_not_supported', 400),
    ({'Content-Range': 'bytes 0-2/3'}, 'attachment_encoding_not_supported', 400),
])
def test_upload_headers_rejected_before_reading(store, changes, code, status):
    class NoRead:
        def read(self, count):
            raise AssertionError('必须在读取正文前拒绝')
    assert rejected(code, lambda: store.receive_upload('event', 'attachment', headers(**changes), NoRead())) == status


@pytest.mark.parametrize('name', ['Content-Length', 'Content-Type', 'X-Content-SHA256'])
def test_duplicate_framing_and_hash_headers_rejected(store, name):
    values = headers()
    values[name] = values[name]
    with pytest.raises(AttachmentRejected):
        store.receive_upload('event', 'attachment', values, BytesIO(BODY))


def test_upload_first_then_message_and_duplicate_content_addressed(store):
    first = store.receive_upload('fictional-event', 'spec-1', headers(), BytesIO(BODY), now=10)
    assert first['persisted'] and not first['duplicate'] and first['status'] == 'orphan'
    store.link_message('message-one', payload(), now=11)
    assert query(store, 'SELECT inbound_id,cleanup_pending FROM attachments') == [('message-one', 0)]
    second = store.receive_upload('fictional-event', 'spec-1', headers(), BytesIO(BODY))
    assert second['duplicate'] and second['status'] == 'linked'
    third = store.receive_upload('fictional-event-other', 'spec-other', headers(), BytesIO(BODY))
    assert third['duplicate']
    assert query(store, 'SELECT COUNT(*) FROM blobs') == [(1,)]
    assert len(list(store.root.glob('*.bin'))) == 1
    assert (store.root / (first['sha256'] + '.bin')).read_bytes() == BODY


def test_message_first_then_upload_and_permissions(store):
    with store.guard():
        store.check_message('message-one', payload())
        store.link_message('message-one', payload())
    assert query(store, 'SELECT sha256,inbound_id FROM attachments') == [(None, 'message-one')]
    uploaded = store.receive_upload('fictional-event', 'spec-1', headers(), BytesIO(BODY))
    assert uploaded['status'] == 'linked'
    assert stat_mode(store.root) == 0o700
    assert stat_mode(store.path) == 0o600
    assert stat_mode(store.root / (uploaded['sha256'] + '.bin')) == 0o600


def stat_mode(path):
    return path.stat().st_mode & 0o777


def test_wrong_hash_and_interrupted_upload_leave_no_binding_and_retry_works(store):
    rejected('attachment_incomplete_body', lambda: store.receive_upload('event', 'spec', headers(), BytesIO(BODY[:3])))
    rejected('attachment_sha256_mismatch', lambda: store.receive_upload('event', 'spec', headers(**{'X-Content-SHA256': '0' * 64}), BytesIO(BODY)))
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]
    assert list(store.root.glob('.upload-*')) == []
    assert not store.receive_upload('event', 'spec', headers(), BytesIO(BODY))['duplicate']


def test_upload_metadata_conflict_never_overwrites_original(store):
    store.link_message('message-one', payload())
    rejected('attachment_metadata_conflict', lambda: store.receive_upload('fictional-event', 'spec-1', headers(body=b'other'), BytesIO(b'other')))
    store.receive_upload('fictional-event', 'spec-1', headers(), BytesIO(BODY))
    rejected('attachment_metadata_conflict', lambda: store.link_message('message-one', payload(attachments=[attachment(filename='different.txt')])))
    assert query(store, 'SELECT filename FROM attachments') == [('fictional-spec.txt',)]


def test_message_aliases_must_have_consistent_metadata(store):
    store.link_message('message-one', payload())
    alias = payload(event_id='other-event')
    store.link_message('message-one', alias)
    store.receive_upload('other-event', 'spec-1', headers(), BytesIO(BODY))
    rejected('attachment_metadata_conflict', lambda: store.link_message('message-one', payload(attachments=[attachment(sha256='0' * 64)])))
    rejected('attachment_event_conflict', lambda: store.link_message('message-two', alias))
    assert query(store, 'SELECT COUNT(*) FROM message_links') == [(2,)]


def test_metadata_batch_conflict_rolls_back_every_item(store):
    store.link_message('message-one', payload())
    batch = payload(attachments=[attachment(attachment_id='new-item'), attachment(size=12)])
    rejected('attachment_metadata_conflict', lambda: store.link_message('message-one', batch))
    assert query(store, 'SELECT attachment_id FROM attachments') == [('spec-1',)]


def test_orphan_age_only_marks_never_deletes_and_late_message_recovers(store):
    upload = store.receive_upload('fictional-event', 'spec-1', headers(), BytesIO(BODY), now=100)
    assert store.mark_orphans(now=100 + 24 * 3600 - 1) == 0
    assert store.mark_orphans(now=100 + 24 * 3600) == 1
    assert store.mark_orphans(now=100 + 25 * 3600) == 0
    assert (store.root / (upload['sha256'] + '.bin')).exists()
    assert query(store, 'SELECT cleanup_pending FROM attachments') == [(1,)]
    store.link_message('message-one', payload())
    assert query(store, 'SELECT cleanup_pending,inbound_id FROM attachments') == [(0, 'message-one')]


def test_capacity_and_duplicate_at_full_capacity(tmp_path):
    store = AttachmentStore(tmp_path / 'db' / 'state.sqlite3', tmp_path / 'files', max_total_bytes=len(BODY))
    store.receive_upload('event', 'spec', headers(), BytesIO(BODY))
    assert store.receive_upload('event-two', 'spec', headers(), BytesIO(BODY))['duplicate']
    assert rejected('attachment_capacity_exceeded', lambda: store.receive_upload('new', 'spec', headers(b'different'), BytesIO(b'different'))) == 507
    assert query(store, 'SELECT COUNT(*) FROM blobs') == [(1,)]


def test_disk_full_is_clear_and_own_temporary_file_is_removed(store, monkeypatch):
    def full(fd, body):
        raise OSError(errno.ENOSPC, 'fictional-full-disk')
    monkeypatch.setattr(module, '_write', full)
    assert rejected('attachment_disk_full', lambda: store.receive_upload('event', 'spec', headers(), BytesIO(BODY))) == 507
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]
    assert list(store.root.glob('.upload-*')) == []


def test_symlink_in_root_or_parent_is_rejected_without_touching_target(tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir(mode=0o700)
    linked = tmp_path / 'linked'
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(AttachmentRejected):
        AttachmentStore(tmp_path / 'db' / 'state.sqlite3', linked / 'child')
    assert list(actual.iterdir()) == []


def test_symlink_db_or_blob_and_insecure_root_are_rejected(tmp_path):
    target = tmp_path / 'target'
    target.write_bytes(b'do not change')
    target.chmod(0o600)
    db_dir = tmp_path / 'db'
    db_dir.mkdir(mode=0o700)
    (db_dir / 'state.sqlite3').symlink_to(target)
    with pytest.raises(AttachmentRejected):
        AttachmentStore(db_dir / 'state.sqlite3', tmp_path / 'files')
    assert target.read_bytes() == b'do not change'
    store = AttachmentStore(tmp_path / 'db-two' / 'state.sqlite3', tmp_path / 'files-two')
    (store.root / (hashlib.sha256(BODY).hexdigest() + '.bin')).symlink_to(target)
    with pytest.raises(AttachmentRejected):
        store.receive_upload('event', 'spec', headers(), BytesIO(BODY))
    assert target.read_bytes() == b'do not change'
    store.root.chmod(0o755)
    rejected('attachment_storage_unsafe', lambda: store.mark_orphans())


def test_concurrent_duplicate_writes_leave_one_blob_and_one_binding(store):
    def send(_):
        return store.receive_upload('event', 'spec', headers(), BytesIO(BODY))
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(send, range(8)))
    assert sum(not reply['duplicate'] for reply in replies) == 1
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(1,)]
    assert query(store, 'SELECT COUNT(*) FROM blobs') == [(1,)]


def test_socket_timeout_maps_to_408_and_removes_staging(store):
    class TimedOut:
        def read(self, count):
            raise TimeoutError('fictional timeout')
    assert rejected('attachment_upload_timeout', lambda: store.receive_upload('event', 'spec', headers(), TimedOut())) == 408
    assert list(store.root.glob('.upload-*')) == []
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]


def test_sqlite_full_maps_to_507(store, monkeypatch):
    def full(db, stamp):
        error = sqlite3.OperationalError('fictional database full')
        error.sqlite_errorcode = sqlite3.SQLITE_FULL
        raise error
    monkeypatch.setattr(store, '_mark', full)
    assert rejected('attachment_disk_full', lambda: store.receive_upload('event', 'spec', headers(), BytesIO(BODY))) == 507
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]
    # 文件已持久化、库未提交时，重复请求复用内容文件并补齐绑定。
    monkeypatch.undo()
    assert store.receive_upload('event', 'spec', headers(), BytesIO(BODY))['duplicate']
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(1,)]


def test_preuploaded_aliases_without_declared_hash_cannot_bind_different_bytes(store):
    other = b'X' * len(BODY)
    store.receive_upload('event-one', 'spec-1', headers(), BytesIO(BODY))
    store.receive_upload('event-two', 'spec-1', headers(other), BytesIO(other))
    metadata = attachment()
    metadata.pop('sha256')
    store.link_message('message-one', payload(event_id='event-one', attachments=[metadata]))
    rejected('attachment_metadata_conflict', lambda: store.link_message('message-one', payload(event_id='event-two', attachments=[metadata])))
    assert query(store, 'SELECT inbound_id FROM message_links WHERE event_id=?', ('event-two',)) == []


@pytest.mark.parametrize('name', ['-wal', '-shm', '-journal'])
def test_sqlite_sidecar_symlinks_rejected_before_open(store, tmp_path, name):
    target = tmp_path / 'do-not-touch'
    target.write_bytes(b'fictional-target')
    target.chmod(0o600)
    sidecar = Path(str(store.path) + name)
    assert not sidecar.exists()
    sidecar.symlink_to(target)
    with pytest.raises(AttachmentRejected):
        store.mark_orphans()
    assert target.read_bytes() == b'fictional-target'


def test_two_store_instances_serialize_on_same_file_lock(store):
    other_store = AttachmentStore(store.path, store.root)
    def send(index):
        target = store if index % 2 else other_store
        return target.receive_upload('event', 'spec', headers(), BytesIO(BODY))
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(send, range(8)))
    assert sum(not reply['duplicate'] for reply in replies) == 1
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(1,)]


@pytest.mark.parametrize('event,identifier', [('..', 'file'), ('event', '../file'), ('event', 'file/child'), ('event', 'a' * 129)])
def test_upload_ids_never_become_paths(store, event, identifier):
    with pytest.raises(AttachmentRejected):
        store.receive_upload(event, identifier, headers(), BytesIO(BODY))
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]


@pytest.mark.parametrize('mime,extensions', sorted(module.MIME_EXTENSIONS.items()))
def test_each_allowed_type_has_matching_safe_metadata(mime, extensions):
    kind = 'voice' if mime.startswith('audio/') else mime.split('/')[0] if mime.startswith(('image/', 'video/')) else 'file'
    for extension in extensions:
        assert validate_fields(payload(attachments=[attachment(mime=mime, kind=kind, filename='fictional' + extension)]))


@pytest.mark.parametrize('cards', [
    [dict(card_type='link', title='同一虚构卡片')] * 2,
    [dict(card_type='product', title=title, product_id='fictional-product') for title in ('虚构卡片一', '虚构卡片二')],
    [dict(card_type='link', title=title, url=url) for title, url in
     [('虚构卡片一', 'https://example.invalid/item?first=discard'),
      ('虚构卡片二', 'https://example.invalid/item?second=discard')]],
])
def test_duplicate_card_identity_is_rejected_before_storage(cards):
    rejected('duplicate_card_identity', lambda: validate_fields(payload(cards=cards)))


def test_card_only_aliases_cannot_link_different_preuploaded_bytes(store):
    other = b'X' * len(BODY)
    store.receive_upload('event-one', 'picture-1', headers(), BytesIO(BODY))
    store.receive_upload('event-two', 'picture-1', headers(other), BytesIO(other))
    card = [dict(card_type='product', title='虚构商品', image_attachment_id='picture-1')]
    store.link_message('message-one', {'event_id': 'event-one', 'cards': card})
    rejected('attachment_metadata_conflict', lambda: store.link_message('message-one', {'event_id': 'event-two', 'cards': card}))
    assert query(store, 'SELECT inbound_id FROM attachments WHERE event_id=?', ('event-two',)) == [(None,)]


def test_unregistered_cas_file_is_only_marked_and_retry_clears_candidate(store):
    digest = hashlib.sha256(BODY).hexdigest()
    orphan = store.root / (digest + '.bin')
    orphan.write_bytes(BODY)
    orphan.chmod(0o600)
    assert store.mark_orphans(now=100) == 0
    assert query(store, 'SELECT filename,first_seen_at,cleanup_pending FROM cleanup_candidates') == [(orphan.name, 100, 0)]
    assert store.mark_orphans(now=100 + 24 * 3600) == 1
    assert query(store, 'SELECT cleanup_pending FROM cleanup_candidates') == [(1,)]
    assert orphan.read_bytes() == BODY
    assert store.receive_upload('event', 'spec', headers(), BytesIO(BODY))['duplicate']
    assert query(store, 'SELECT COUNT(*) FROM cleanup_candidates') == [(0,)]
    assert orphan.read_bytes() == BODY


def test_crash_staging_file_is_only_marked_and_counts_towards_capacity(tmp_path):
    store = AttachmentStore(tmp_path / 'db' / 'state.sqlite3', tmp_path / 'files', max_total_bytes=len(BODY))
    orphan = store.root / ('.upload-' + 'a' * 32)
    orphan.write_bytes(BODY)
    orphan.chmod(0o600)
    assert store.mark_orphans(now=100) == 0
    assert store.mark_orphans(now=100 + 24 * 3600) == 1
    assert query(store, 'SELECT filename,cleanup_pending FROM cleanup_candidates') == [(orphan.name, 1)]
    assert rejected('attachment_capacity_exceeded', lambda: store.receive_upload('event', 'spec', headers(), BytesIO(BODY))) == 507
    assert orphan.read_bytes() == BODY


def test_cleanup_candidate_scan_rejects_symlinks_and_never_reads_target(store, tmp_path):
    target = tmp_path / 'outside'
    target.write_bytes(b'fictional target')
    (store.root / ('.upload-' + 'a' * 32)).symlink_to(target)
    with pytest.raises(AttachmentRejected):
        store.mark_orphans()
    assert target.read_bytes() == b'fictional target'
    assert query(store, 'SELECT COUNT(*) FROM cleanup_candidates') == [(0,)]


def test_one_event_cannot_upload_more_than_twenty_distinct_attachments(store):
    for index in range(20):
        store.receive_upload('event', 'file-' + str(index), headers(), BytesIO(BODY))
    rejected('attachment_event_limit', lambda: store.receive_upload('event', 'file-20', headers(), BytesIO(BODY)))
    assert store.receive_upload('event', 'file-0', headers(), BytesIO(BODY))['duplicate']
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(20,)]


def test_bulk_alias_preflight_detects_conflicts_before_any_alias_is_linked(store):
    other = b'X' * len(BODY)
    store.receive_upload('event-one', 'picture-1', headers(), BytesIO(BODY))
    store.receive_upload('event-two', 'picture-1', headers(other), BytesIO(other))
    card = [dict(card_type='product', title='虚构商品', image_attachment_id='picture-1')]
    aliases = [{'event_id': event, 'cards': card} for event in ('event-one', 'event-two')]
    rejected('attachment_metadata_conflict', lambda: store.check_messages('message-one', aliases))
    rejected('attachment_metadata_conflict', lambda: store.link_messages('message-one', aliases))
    assert query(store, 'SELECT COUNT(*) FROM message_links') == [(0,)]
    assert query(store, 'SELECT DISTINCT inbound_id FROM attachments') == [(None,)]


def test_bulk_alias_link_failure_rolls_back_all_aliases_and_retry_repairs(store, monkeypatch):
    store.receive_upload('event-one', 'spec-1', headers(), BytesIO(BODY))
    aliases = [payload(event_id=event) for event in ('event-one', 'event-two', 'event-three')]
    original = store._link_message
    def partial_failure(db, inbound_id, value, stamp):
        if value['event_id'] == 'event-two':
            raise AttachmentRejected('attachment_storage_unavailable', 503)
        original(db, inbound_id, value, stamp)
    monkeypatch.setattr(store, '_link_message', partial_failure)
    rejected('attachment_storage_unavailable', lambda: store.link_messages('message-one', aliases))
    assert query(store, 'SELECT COUNT(*) FROM message_links') == [(0,)]
    assert query(store, 'SELECT inbound_id FROM attachments') == [(None,)]
    monkeypatch.setattr(store, '_link_message', original)
    store.link_messages('message-one', aliases)
    assert query(store, 'SELECT COUNT(*) FROM message_links WHERE inbound_id=?', ('message-one',)) == [(3,)]
    assert query(store, 'SELECT COUNT(*) FROM attachments WHERE inbound_id=?', ('message-one',)) == [(3,)]
    assert query(store, 'SELECT COUNT(*) FROM attachments WHERE inbound_id=? AND sha256 IS NOT NULL', ('message-one',)) == [(1,)]


def test_bulk_alias_different_declared_hashes_are_rejected_without_upload(store):
    aliases = [payload(event_id='event-one'), payload(event_id='event-two', attachments=[attachment(sha256='0' * 64)])]
    rejected('attachment_metadata_conflict', lambda: store.link_messages('message-one', aliases))
    assert query(store, 'SELECT COUNT(*) FROM message_links') == [(0,)]
    assert query(store, 'SELECT COUNT(*) FROM attachments') == [(0,)]
