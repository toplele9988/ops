import http.client
import json
import sqlite3
import socket
import time
from urllib.parse import quote

import pytest

from entities_fixtures import batch, entities, entity, message

PATH = entities.ENTITY_PATH


def request(server, method, path, payload=None, *, auth=True, headers=None):
    assert server.server_address[0] == '127.0.0.1' and server.server_address[1] != 8791
    supplied = dict(headers or {})
    if auth:
        supplied['Authorization'] = 'Bearer ' + server.token
    if isinstance(payload, dict):
        payload = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        supplied['Content-Type'] = 'application/json'
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request(method, path, payload, supplied)
        response = connection.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        connection.close()


def test_real_http_batch_readback_retry_stats_and_chat_unchanged(server):
    first = request(server, 'POST', '/api/inbound/alibaba', message())
    assert first[0] == 202
    with server.store.connect() as db:
        before = list(db.iterdump())
    payload = batch(values=[entity(entity_id='fictional:order/123+@=')])
    code, result, headers = request(server, 'POST', PATH, payload)
    assert code == 201 and result['persisted'] and result['versions_added'] == 1
    assert headers['Cache-Control'] == 'no-store' and headers['X-Content-Type-Options'] == 'nosniff'
    duplicate = request(server, 'POST', PATH, payload)
    assert duplicate[0] == 200 and duplicate[1] == dict(result, duplicate=True)
    target = PATH + '/order/' + quote(payload['entities'][0]['entity_id'], safe='')
    code, saved, _ = request(server, 'GET', target)
    assert code == 200 and saved['entity']['fields'] == entity()['fields']
    stats = request(server, 'GET', PATH + '/stats')
    assert stats[0] == 200 and stats[1]['total_entities'] == 1
    assert stats[1]['recent_batches'][0]['batch_id'] == payload['batch_id']
    with server.store.connect() as db:
        assert list(db.iterdump()) == before
    assert request(server, 'GET', '/api/inbound/alibaba/' + first[1]['inbound_id'])[0] == 200


@pytest.mark.parametrize('method,path', [('POST', PATH), ('GET', PATH + '/stats'), ('GET', PATH + '/order/fictional')])
def test_auth_and_origin_required_for_every_entity_route(server, method, path):
    payload = batch() if method == 'POST' else None
    assert request(server, method, path, payload, auth=False)[0] == 401
    assert request(server, method, path, payload, headers={'Origin': 'https://example.invalid'})[0] == 403
    assert server.entities.stats()['total_batches'] == 0


def test_preflight_has_same_origin_and_header_controls(server):
    headers = {'Origin': 'https://message.alibaba.com', 'Access-Control-Request-Method': 'POST',
               'Access-Control-Request-Headers': 'authorization,content-type'}
    code, _, response = request(server, 'OPTIONS', PATH, auth=False, headers=headers)
    assert code == 200 and response['Access-Control-Allow-Origin'] == headers['Origin']
    for key, value in [('Access-Control-Request-Method', 'PUT'),
                       ('Access-Control-Request-Headers', 'X-Unknown'), ('Origin', 'https://example.invalid')]:
        assert request(server, 'OPTIONS', PATH, auth=False, headers=dict(headers, **{key: value}))[0] == 403
    assert request(server, 'OPTIONS', PATH + '/stats', auth=False,
                   headers=dict(headers, **{'Access-Control-Request-Method': 'GET'}))[0] == 200


@pytest.mark.parametrize('raw,headers,code,error', [
    (b'{}', {'Content-Type': 'text/plain'}, 415, 'json_required'),
    (b'{}', {'Content-Type': 'application/json', 'Transfer-Encoding': 'chunked'}, 400, 'encoding_not_supported'),
    (b'{}', {'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}, 400, 'encoding_not_supported'),
    (b'x' * 65537, {'Content-Type': 'application/json'}, 413, 'body_too_large'),
    (b'{"batch_id":"a","batch_id":"b"}', {'Content-Type': 'application/json'}, 400, 'duplicate_json_key'),
    (b'{"fields":NaN}', {'Content-Type': 'application/json'}, 400, 'invalid_json'),
    (b'\xff', {'Content-Type': 'application/json'}, 400, 'invalid_json'),
])
def test_existing_body_limits_and_strict_json_apply(server, raw, headers, code, error):
    reply = request(server, 'POST', PATH, raw, headers=headers)
    assert reply[0] == code and reply[1]['error'] == error and not reply[1]['persisted']
    assert server.entities.stats()['total_batches'] == 0


def test_sql_failure_is_sanitized_and_not_persisted(server):
    with server.entities.connect() as db:
        db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON entity_batches BEGIN SELECT RAISE(ABORT,'fictional private failure'); END")
    code, reply, _ = request(server, 'POST', PATH, batch())
    assert code == 503 and not reply['persisted']
    assert 'fictional private' not in json.dumps(reply) and 'Traceback' not in json.dumps(reply)
    assert server.entities.stats()['total_entities'] == 0


def test_missing_records_and_invalid_routes(server):
    for path in (PATH, PATH + '/invalid/id', PATH + '/stats?x=1', PATH + '/order/missing'):
        assert request(server, 'GET', path)[0] == 404
    for path in (PATH + '/order/%ZZ', PATH + '/order/%FF', PATH + '/order/%00'):
        assert request(server, 'GET', path)[0] == 400


def test_no_downstream_or_attachments_used_for_entity_write(server, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('entity route must not touch chat or attachments')
    monkeypatch.setattr(server.store, 'receive', forbidden)
    monkeypatch.setattr(server.store.attachments, 'readback', forbidden)
    assert request(server, 'POST', PATH, batch())[0] == 201
    assert not server.wake.is_set()


def test_optional_idempotency_header_must_match_batch(server):
    payload = batch()
    assert request(server, 'POST', PATH, payload, headers={'X-Idempotency-Key': 'wrong'})[1]['error'] == 'entity_batch_header_mismatch'
    assert request(server, 'POST', PATH, payload, headers={'X-Idempotency-Key': payload['batch_id']})[0] == 201
    assert request(server, 'POST', PATH, payload, headers={'X-Idempotency-Key': payload['batch_id']})[0] == 200


@pytest.mark.parametrize('bad_header,status,error', [
    ('Content-Length', 411, 'content_length_required'),
    ('Authorization', 401, 'unauthorized'),
    ('X-Idempotency-Key', 400, 'invalid_entity_batch_id'),
])
def test_duplicate_headers_not_silently_accepted(server, bad_header, status, error):
    raw = json.dumps(batch()).encode()
    headers = {'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json',
               'Content-Length': str(len(raw)), 'X-Idempotency-Key': batch()['batch_id']}
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.putrequest('POST', PATH)
        for key, value in headers.items():
            connection.putheader(key, value)
        connection.putheader(bad_header, headers[bad_header])
        connection.endheaders(raw)
        response = connection.getresponse()
        assert response.status == status and json.loads(response.read())['error'] == error
    finally:
        connection.close()
    assert server.entities.stats()['total_batches'] == 0


def test_overload_keeps_existing_connection_cap_and_allows_safe_retry(server):
    acquired = []
    try:
        while server.slots.acquire(blocking=False):
            acquired.append(True)
        with pytest.raises((ConnectionError, http.client.RemoteDisconnected)):
            request(server, 'POST', PATH, batch())
        assert server.entities.stats()['total_batches'] == 0
    finally:
        for _ in acquired:
            server.slots.release()
    assert request(server, 'POST', PATH, batch())[0] == 201


@pytest.mark.parametrize('phase', ['headers', 'entity_body'])
def test_entity_connections_keep_absolute_deadline_during_continuous_trickle(server, monkeypatch, phase):
    monkeypatch.setattr(server.RequestHandlerClass, 'header_seconds', .15)
    monkeypatch.setattr(server.RequestHandlerClass, 'request_seconds', .15)
    with socket.create_connection(server.server_address, timeout=2) as connection:
        if phase == 'headers':
            connection.sendall(b'POST ' + PATH.encode() + b' HTTP/1.0\r\nX-Fictional: ')
        else:
            head = ('POST ' + PATH + ' HTTP/1.0\r\nAuthorization: Bearer ' + server.token +
                    '\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n')
            connection.sendall(head.encode())
        closed = False
        for _ in range(20):
            try:
                connection.sendall(b'x')
            except OSError:
                closed = True
                break
            time.sleep(.02)
        connection.settimeout(.01)
        try:
            closed = closed or connection.recv(1024) == b''
        except socket.timeout:
            pass
        assert closed
    assert server.entities.stats()['total_batches'] == 0
