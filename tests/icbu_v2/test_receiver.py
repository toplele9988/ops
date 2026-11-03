from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import hashlib
import http.client
import importlib.util
import io
import json
import sqlite3
import sys
import threading

import pytest

from receiver_fixtures import ROOT, attachment, load_receiver, message
from icbu_attachments import AttachmentStore


receiver = load_receiver()


@pytest.fixture
def store(tmp_path):
    sidecar = AttachmentStore(tmp_path / 'metadata' / 'attachments.sqlite3', tmp_path / 'blobs')
    return receiver.Store(tmp_path / 'inbound.sqlite3', attachments=sidecar)


def saved(store):
    with store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM inbound')]


def headers(body, mime='text/plain', digest=None):
    values = Message()
    values['Content-Length'] = str(len(body))
    values['Content-Type'] = mime
    values['X-Content-SHA256'] = digest or hashlib.sha256(body).hexdigest()
    return values


def test_legacy_five_fields_and_current_message_keep_original_fingerprint(store):
    legacy = {key: message()[key] for key in ('account', 'conversation_id', 'buyer_name', 'content', 'url')}
    normalized = receiver.validate(legacy)
    assert normalized['role'] == 'buyer'
    assert normalized['role_assumption'] == 'legacy_contract'
    assert not receiver.V2_FIELDS.intersection(normalized)
    # This is the production v1 fingerprint, computed from its documented core.
    assert receiver.fingerprint(normalized) == '92cb496304f93755a07180a64392355bc799b1fcd2c2507552b1bb572dc8ed8a'
    current = receiver.validate(message())
    assert receiver.fingerprint(current) == '097e06a7cfae8dec839905e75517c29da5009e768b8de3a20423fe94958e0321'
    enriched = receiver.validate(message(message_kind='file', attachments=[attachment()]))
    assert receiver.fingerprint(enriched) == receiver.fingerprint(current)
    first, second = store.receive(normalized), store.receive(normalized)
    assert first['inbound_id'] != second['inbound_id']  # Legacy messages without IDs stay append-only.


def test_enrichment_updates_existing_record_without_changing_hash_state_or_job(store):
    first = store.receive(receiver.validate(message()))
    before = saved(store)[0]
    response = store.receive(receiver.validate(message(message_kind='file', attachments=[attachment()], cards=[
        dict(card_type='product', title='虚构商品', url='https://www.alibaba.com/fictional')])) )
    after = saved(store)[0]
    assert response['duplicate'] and response['inbound_id'] == first['inbound_id']
    assert len(saved(store)) == 1
    assert after['fingerprint'] == before['fingerprint']
    assert after['state'] == before['state'] == 'archived'
    assert after['created_at'] == before['created_at']
    assert json.loads(after['payload'])['attachments'] == [attachment()]
    # A plain legacy replay never erases rich metadata.
    store.receive(receiver.validate(message()))
    assert saved(store)[0]['payload'] == after['payload']


def test_event_only_legacy_message_enrichment_preserves_dedup_fingerprint(store):
    plain = receiver.validate(message(message_id=None))
    first = store.receive(plain)
    original = saved(store)[0]['fingerprint']
    rich = receiver.validate(message(message_id=None, attachments=[attachment()]))
    result = store.receive(rich)
    assert receiver.fingerprint(rich) == receiver.fingerprint(plain) == original
    assert result['duplicate'] and result['inbound_id'] == first['inbound_id']
    assert result['dedup_mode'] == 'event_id' and len(saved(store)) == 1


@pytest.mark.parametrize('key,value', [
    ('attachments', 'file'), ('attachments', [{}] * 21), ('cards', [{}] * 11),
    ('message_kind', 'animation'), ('message_kind', []),
    ('attachments', [attachment(filename='../fictional.txt')]),
    ('attachments', [attachment(filename='fictional.svg')]),
    ('attachments', [attachment(filename='fictional.html')]),
    ('attachments', [attachment(filename='fake.txt.exe')]),
    ('attachments', [attachment(size=True)]), ('attachments', [attachment(size=50 * 1024 ** 2 + 1)]),
    ('attachments', [attachment(mime='text/html')]), ('attachments', [attachment(kind='unknown')]),
    ('attachments', [attachment(filename='x' * 201)]),
    ('attachments', [attachment(attachment_id='a/b')]),
    ('attachments', [attachment(sha256='g' * 64)]),
    ('cards', [dict(card_type='product', title='x' * 301)]),
    ('cards', [dict(card_type='product', title='虚构', url='http://example.invalid/')]),
    ('cards', [dict(card_type='product', title='虚构', url='https://user:pass@example.invalid/')]),
    ('cards', [dict(card_type='product', title='虚构', url='https://example.invalid/has space')]),
    ('cards', [dict(card_type='javascript', title='虚构')]),
    ('cards', [dict(card_type='link', title='虚构', unknown='x')]),
])
def test_v2_invalid_boundaries_rejected_clearly(key, value):
    with pytest.raises(receiver.Rejected) as caught:
        receiver.validate(message(**{key: value}))
    explanation = receiver.rejection_message(caught.value.code)
    assert explanation and any('\u4e00' <= char <= '\u9fff' for char in explanation)


@pytest.mark.parametrize('payload', [
    dict(content=''), dict(country=123), dict(history=1), dict(role='admin'), dict(event_id='has space'),
    dict(sent_at='2026-10-30'), dict(url='http://message.alibaba.com/'),
    dict(url='https://message.alibaba.com.evil.invalid/'), dict(unknown='value'),
])
def test_existing_invalid_payloads_stay_rejected(payload):
    with pytest.raises(receiver.Rejected):
        receiver.validate(message(**payload))


def test_empty_content_requires_rich_content_and_ids_remain_stable(store):
    value = receiver.validate(message(content='', attachments=[attachment()], message_kind='file'))
    assert value['content'] == ''
    assert store.receive(value)['duplicate'] is False
    assert store.receive(value)['duplicate'] is True
    with pytest.raises(receiver.Rejected):
        receiver.validate(message(content='', attachments=[]))
    with pytest.raises(receiver.Rejected):
        receiver.validate(message(event_id=None, attachments=[attachment()]))


def test_header_event_can_supply_v2_id():
    payload = message(event_id=None, attachments=[attachment()])
    assert receiver.validate(payload, 'fictional-header-event')['event_id'] == 'fictional-header-event'
    with pytest.raises(receiver.Rejected):
        receiver.validate(payload, 'event/unsafe')


def test_alias_new_event_links_same_message_and_late_upload(store):
    original = store.receive(receiver.validate(message()))
    payload = receiver.validate(message(event_id='fictional-event-alias', attachments=[attachment()]))
    result = store.receive(payload)
    assert result['inbound_id'] == original['inbound_id'] and result['duplicate']
    body = b'fictional attachment\n'
    reply = store.attachments.receive_upload(payload['event_id'], 'fictional-file', headers(body), io.BytesIO(body))
    with sqlite3.connect(store.attachments.path) as db:
        row = db.execute('SELECT inbound_id,sha256 FROM attachments WHERE event_id=?', (payload['event_id'],)).fetchone()
    assert row == (original['inbound_id'], hashlib.sha256(body).hexdigest())
    assert reply['persisted']


def test_enrichment_conflict_rolls_back_and_does_not_replace_payload(store):
    store.receive(receiver.validate(message(attachments=[attachment()])))
    original = saved(store)[0]['payload']
    with pytest.raises(receiver.Rejected) as caught:
        store.receive(receiver.validate(message(attachments=[attachment(size=8)])))
    assert caught.value.status == 409
    assert saved(store)[0]['payload'] == original


def test_upload_before_message_conflict_does_not_create_message(store):
    body = b'fictional attachment\n'
    store.attachments.receive_upload('fictional-event-1', 'fictional-file', headers(body), io.BytesIO(body))
    with pytest.raises(receiver.Rejected) as caught:
        store.receive(receiver.validate(message(attachments=[attachment(size=1)])))
    assert caught.value.status == 409
    assert saved(store) == []


def test_missing_sha_can_be_filled_but_existing_hash_is_immutable(store):
    declaration = attachment()
    declaration.pop('sha256')
    first = store.receive(receiver.validate(message(attachments=[declaration])))
    result = store.receive(receiver.validate(message(attachments=[attachment()])))
    assert result['duplicate'] and result['inbound_id'] == first['inbound_id']
    with pytest.raises(receiver.Rejected):
        store.receive(receiver.validate(message(attachments=[attachment(sha256='0' * 64)])))


def test_concurrent_enrichment_creates_one_message(store):
    value = receiver.validate(message(attachments=[attachment()]))
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: store.receive(value), range(12)))
    assert len({item['inbound_id'] for item in responses}) == 1
    assert sum(not item['duplicate'] for item in responses) == 1
    assert len(saved(store)) == 1


def test_sidecar_commit_failure_replay_repairs_without_second_message(store, monkeypatch):
    value = receiver.validate(message(attachments=[attachment()]))
    original = store.attachments.link_messages
    def fail(*args, **kwargs):
        from icbu_attachments import AttachmentRejected
        raise AttachmentRejected('attachment_storage_unavailable', 503)
    monkeypatch.setattr(store.attachments, 'link_messages', fail)
    with pytest.raises(receiver.Rejected):
        store.receive(value)
    assert len(saved(store)) == 1
    monkeypatch.setattr(store.attachments, 'link_messages', original)
    assert store.receive(value)['duplicate']
    assert len(saved(store)) == 1


def test_alias_replay_repairs_all_earlier_failed_links(store, monkeypatch):
    body = b'fictional attachment\n'
    store.attachments.receive_upload('fictional-event-1', 'fictional-file', headers(body), io.BytesIO(body))
    original = store.attachments.link_messages
    def fail(*args, **kwargs):
        from icbu_attachments import AttachmentRejected
        raise AttachmentRejected('attachment_storage_unavailable', 503)
    monkeypatch.setattr(store.attachments, 'link_messages', fail)
    for event in ('fictional-event-1', 'fictional-failed-alias'):
        with pytest.raises(receiver.Rejected):
            store.receive(receiver.validate(message(event_id=event, attachments=[attachment()])))
    assert len(saved(store)) == 1
    monkeypatch.setattr(store.attachments, 'link_messages', original)
    result = store.receive(receiver.validate(message(event_id='fictional-repair-alias', attachments=[attachment()])))
    assert result['duplicate']
    with sqlite3.connect(store.attachments.path) as db:
        links = db.execute('SELECT event_id,inbound_id FROM message_links').fetchall()
        uploaded = db.execute('SELECT inbound_id FROM attachments WHERE event_id=? AND sha256 IS NOT NULL',
                              ('fictional-event-1',)).fetchone()
    assert len(links) == 3 and {row[1] for row in links} == {result['inbound_id']}
    assert uploaded == (result['inbound_id'],)


def test_cumulative_metadata_limits_are_enforced_across_enrichment(store):
    files = [attachment(attachment_id='file-' + str(index)) for index in range(20)]
    store.receive(receiver.validate(message(attachments=files)))
    with pytest.raises(receiver.Rejected) as caught:
        store.receive(receiver.validate(message(attachments=[attachment(attachment_id='one-too-many')])))
    assert caught.value.code == 'too_many_attachments'
    assert len(json.loads(saved(store)[0]['payload'])['attachments']) == 20


def test_background_orphan_scan_does_not_block_http_accept_loop(store, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def slow_scan(**kwargs):
        started.set()
        release.wait(timeout=5)
    monkeypatch.setattr(store.attachments, 'mark_orphans', slow_scan)
    server = receiver.Server(('127.0.0.1', 0), store, 'fictional-test-token',
                             import_archive=object(), probe_heartbeats=object())
    try:
        server.service_actions()
        assert started.wait(timeout=1)
        # A second maintenance tick also returns while the first scan is busy.
        server._orphan_check_at = 0
        server.service_actions()
        assert not release.is_set()
    finally:
        release.set()
        if server._maintenance:
            server._maintenance.join(timeout=5)
        server.server_close()


def test_cumulative_cards_and_serialized_size_reject_without_replacing_saved_data(store):
    cards = [dict(card_type='product', title='虚构商品' + str(index)) for index in range(10)]
    store.receive(receiver.validate(message(cards=cards)))
    original = saved(store)[0]['payload']
    with pytest.raises(receiver.Rejected) as caught:
        store.receive(receiver.validate(message(cards=[dict(card_type='link', title='多余卡片')])))
    assert caught.value.code == 'too_many_cards'
    assert saved(store)[0]['payload'] == original
    def large_cards(start):
        return [dict(card_type='product', title='卡' * 300, product_id='fictional-' + str(index),
                     url='https://example.invalid/' + 'x' * 3900, subtitle='S' * 1000,
                     price_text='P' * 200, moq_text='M' * 200) for index in range(start, start + 5)]
    base = message(message_id='fictional-large-message', event_id='fictional-large-event', content='X' * 16000)
    first = receiver.validate(dict(base, cards=large_cards(0)))
    assert len(receiver.compact(first).encode()) <= receiver.MAX_BODY
    store.receive(first)
    with pytest.raises(receiver.Rejected) as caught:
        store.receive(receiver.validate(dict(base, cards=large_cards(5))))
    assert caught.value.code == 'v2_metadata_too_large' and caught.value.status == 413
    assert len(saved(store)) == 2


def test_enrichment_does_not_queue_another_live_job(tmp_path, monkeypatch):
    admissions, jobs = [], []
    monkeypatch.setattr(receiver.live, 'install_schema', lambda db: None, raising=False)
    monkeypatch.setattr(receiver.live, 'admit', lambda payload, policy, now: (admissions.append(payload) or True, None), raising=False)
    monkeypatch.setattr(receiver.live, 'insert_job', lambda db, identifier, payload, policy, now: jobs.append(identifier), raising=False)
    storage = receiver.Store(tmp_path / 'live-inbound.sqlite3', live_policy={'fictional': True})
    first = storage.receive(receiver.validate(message(history=False, role='buyer')))
    second = storage.receive(receiver.validate(message(history=False, role='buyer', attachments=[attachment()])))
    assert second['duplicate'] and second['inbound_id'] == first['inbound_id']
    assert len(admissions) == len(jobs) == 1
    assert saved(storage)[0]['state'] == 'draft_queued'


@pytest.fixture
def http_server(store):
    server = receiver.Server(('127.0.0.1', 0), store, 'fictional-test-token-for-p2b-' + 'x' * 12,
                             import_archive=object(), probe_heartbeats=object())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        result = connection.getresponse()
        return result.status, json.loads(result.read()), dict(result.getheaders())
    finally:
        connection.close()


def test_http_auth_source_framing_and_cors(http_server):
    server = http_server
    path = '/api/inbound/alibaba/attachments/fictional-event/file-1'
    body = b'fictional'
    valid = dict(headers(body).items(), Authorization='Bearer ' + server.token)
    assert request(server, 'PUT', path, body, {})[0] == 401
    assert request(server, 'PUT', path, body, dict(valid, Origin='https://evil.invalid'))[0] == 403
    assert request(server, 'PUT', path, body, dict(valid, **{'Content-Type': 'text/html'}))[0] == 415
    assert request(server, 'PUT', path, body, dict(valid, **{'Transfer-Encoding': 'chunked'}))[0] == 400
    bad = dict(valid)
    del bad['X-Content-SHA256']
    assert request(server, 'PUT', path, body, bad)[0] == 400
    code, reply, _ = request(server, 'PUT', path, body, valid)
    assert code == 201 and not reply['duplicate']
    assert request(server, 'PUT', path, body, valid)[1]['duplicate']
    assert request(server, 'PUT', '/api/inbound/alibaba/attachments/../bad', body, valid)[0] == 400
    code, _, response_headers = request(server, 'OPTIONS', path, headers={
        'Origin': 'https://message.alibaba.com', 'Access-Control-Request-Method': 'PUT',
        'Access-Control-Request-Headers': 'authorization, content-type, content-length, x-content-sha256'})
    assert code == 200 and 'PUT' in response_headers['Access-Control-Allow-Methods']
    assert not receiver.peer_allowed('8.8.8.8')
    assert receiver.peer_allowed('127.0.0.1')


def test_win10_selftest_runs_against_real_candidate_http(http_server):
    spec = importlib.util.spec_from_file_location('p2b_selftest_integration', ROOT / 'scripts' / 'icbu_v2_selftest.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    target = module.target_for('http://127.0.0.1:' + str(http_server.server_address[1]))
    lines = []
    module.run(module.Client(target, http_server.token), output=lines.append)
    assert '自测完成' in lines[-1]
    assert len(saved(http_server.store)) == 2
