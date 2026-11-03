"""Synthetic sources only. Block every network and SQLite access outside tmp_path."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from console.archive.store import Store
from console.inbox.model import make_key
from console.ops_queue import OpsQueue
from console import v3_ops_queue_api

NOW = 1893456000  # 2030-01-01 08:00 Beijing
AT = '2030-01-01T00:00:00+00:00'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('network forbidden in queue tests')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)
    original = sqlite3.connect
    def connect(path, *args, **kwargs):
        assert str(tmp_path) in str(path), 'only temporary fictional databases allowed'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)


@pytest.fixture
def env(tmp_path):
    path = tmp_path / 'fictional.sqlite3'
    store = Store(path, target_version=8)
    with store.write() as db:
        for did, stage, status in [(1, 'awaiting_payment', 'open'), (2, 'paid', 'won')]:
            db.execute('INSERT INTO deals(id,title,stage,status,created_at,updated_at,stage_changed_at) VALUES(?,?,?,?,?,?,?)',
                       (did, '虚构灯具案子' + str(did), stage, status, AT, AT, AT))
        db.execute("INSERT INTO deal_payments(id,deal_id,kind,amount_fen,currency,received_on,method,created_at,created_by,updated_at) "
                   "VALUES(1,1,'deposit',100,'USD','2030-01-01','wire',?,'fictional',?)", (AT, AT))
        db.execute("INSERT INTO quote_rounds(id,deal_id,round_no,status,valid_until,created_at,updated_at) VALUES(1,1,1,'sent','2030-01-02',?,?)", (AT, AT))
        db.execute("INSERT INTO fulfilment_nodes(id,deal_id,node,status,planned_on,created_at,updated_at) "
                   "VALUES(1,2,'production','doing','2029-12-30',?,?)", (AT, AT))
        db.execute("INSERT INTO role_runs(id,role,subject_kind,subject_key,input_fingerprint,status,source,output_json,output_text,evidence_json,created_at,created_by) "
                   "VALUES(1,'triage','day','fictional','fake','ok','rules','{}','虚构建议','[]',?,'fictional')", (AT,))
        db.execute("INSERT INTO triage_suggestions(id,conversation_key,action,reason,confidence,source,run_id,summary_json,created_at,updated_at) "
                   "VALUES(1,'fictional-unassigned','identity','虚构待核对',0.5,'rules',1,'{}',?,?)", (AT, AT))
    key = make_key('conversation', 'alibaba', 'fictional', 'buyer')
    supplier_key = make_key('conversation', 'wechat', 'fictional', 'supplier')
    conv = lambda key, role, speaker, age: dict(key=key, channel='alibaba', buyer_name='虚构联系人',
        counterpart_role=role, waiting_reply=speaker == 'buyer', dormant=False,
        messages=[dict(role=speaker, sent_at=NOW-age*3600, text='虚构产品需求')])
    conversations = {key: conv(key, 'customer', 'buyer', 25), supplier_key: conv(supplier_key, 'supplier', 'seller', 49)}
    inbox = SimpleNamespace(read=Mock(return_value=dict(ready=True, conversations=conversations)))
    overview = SimpleNamespace(read=Mock(return_value=dict(channels=[dict(key='alibaba', label='阿里国际站',
        stages=[dict(key='collecting', label='实时采集', state='interrupted')])])))
    pipeline = SimpleNamespace(read=Mock(return_value=dict(failed_jobs=dict(count=0))))
    def service(**extra):
        return OpsQueue(paths={'archive': path}, inbox=inbox, overview=overview, pipeline=pipeline,
                        clock=lambda: NOW, **extra)
    return SimpleNamespace(store=store, path=path, key=key, supplier_key=supplier_key,
                           inbox=inbox, overview=overview, pipeline=pipeline, service=service)


def test_all_seven_kinds_sort_context_and_read_only(env):
    before = (env.path.read_bytes(), env.path.stat().st_mtime_ns)
    result = env.service().read()
    assert result['complete'] and result['total'] == 7
    assert set(result['counts']) == {i['kind'] for i in result['items']}
    assert all(n == 1 for n in result['counts'].values())
    assert result['items'][0]['kind'] == 'pipeline'
    assert [i['priority'] for i in result['items']] == sorted(i['priority'] for i in result['items'])
    buyer = next(i for i in result['items'] if i['kind'] == 'buyer_reply')
    assert 'conversation=' + env.key in buyer['to'] and 'c=' + env.key in buyer['to']
    assert all(i['to'].startswith('/deals/') for i in result['items'] if i['kind'] in ('payment', 'quote_expiry', 'logistics'))
    assert 'conversation=fictional-unassigned' in next(i for i in result['items'] if i['kind'] == 'ownership')['to']
    assert before == (env.path.read_bytes(), env.path.stat().st_mtime_ns)
    assert env.service().read() == result


def test_real_source_changes_remove_items_and_revision_changes(env):
    old = env.service().read()
    env.inbox.read.return_value['conversations'][env.key]['messages'][0]['text'] = '虚构新需求'
    new = env.service().read()
    assert old['items'][2:] != new['items'][2:]
    with env.store.write() as db:
        db.execute("UPDATE deal_payments SET status='void',voided_at=?,voided_by='fictional' WHERE id=1", (AT,))
        db.execute("UPDATE quote_rounds SET status='superseded'")
        db.execute("UPDATE fulfilment_nodes SET status='done'")
        db.execute("UPDATE triage_suggestions SET status='dismissed'")
    env.inbox.read.return_value['conversations'] = {}
    env.overview.read.return_value = dict(channels=[])
    result = env.service().read()
    assert result['items'] == [] and result['complete']


@pytest.mark.parametrize('age,expected', [(47.99, 0), (48, 1), (72, 1)])
def test_supplier_chase_threshold(env, age, expected):
    env.inbox.read.return_value['conversations'][env.supplier_key]['messages'][0]['sent_at'] = NOW-age*3600
    assert env.service().read()['counts']['supplier_chase'] == expected


@pytest.mark.parametrize('role', ['unclassified', 'internal', 'learning', 'unknown'])
def test_unknown_is_not_counted_as_buyer(env, role):
    env.inbox.read.return_value['conversations'][env.key]['counterpart_role'] = role
    assert env.service().read()['counts']['buyer_reply'] == 0


@pytest.mark.parametrize('due,expected', [('2030-01-04', 1), ('2030-01-05', 0), (None, 0), ('2029-12-01', 1)])
def test_quote_window_and_missing_date(env, due, expected):
    with env.store.write() as db:
        db.execute('UPDATE quote_rounds SET valid_until=?', (due,))
    assert env.service().read()['counts']['quote_expiry'] == expected


def test_latest_quote_only(env):
    with env.store.write() as db:
        db.execute("INSERT INTO quote_rounds(deal_id,round_no,status,valid_until,created_at,updated_at) VALUES(1,2,'sent','2030-02-01',?,?)", (AT, AT))
    assert env.service().read()['counts']['quote_expiry'] == 0


def test_eta_after_booking_done_and_promise_after_confirmation_done(env):
    with env.store.write() as db:
        db.execute("UPDATE fulfilment_nodes SET status='done'")
        db.execute("INSERT INTO fulfilment_nodes(deal_id,node,status,eta,created_at,updated_at) VALUES(2,'booking','done','2029-12-30',?,?)", (AT, AT))
    assert env.service().read()['counts']['logistics'] == 1
    with env.store.write() as db:
        db.execute("UPDATE deals SET stage='delivered' WHERE id=2")
    assert env.service().read()['counts']['logistics'] == 0


def test_failures_are_partial_safe_and_never_empty_success(env):
    env.inbox.read.side_effect = RuntimeError('private-canary')
    env.overview.read.side_effect = RuntimeError('private-canary')
    env.pipeline.read.side_effect = RuntimeError('private-canary')
    result = env.service().read()
    assert not result['complete'] and result['counts']['payment'] == 1
    assert 'private-canary' not in json.dumps(result)


def test_partial_paths_disable_every_other_source_without_creation(tmp_path, monkeypatch):
    import os
    original_open = os.open
    def local_open(path, *args, **kwargs):
        assert str(tmp_path) in str(path), 'explicit paths must also isolate JSON configuration'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', local_open)
    service = OpsQueue(paths={'archive': tmp_path / 'missing.sqlite3'}, clock=lambda: NOW)
    assert all(str(tmp_path) in str(p) for p in service.paths.values())
    assert all(str(tmp_path) in str(p) for p in service.overview.paths.values())
    assert all(str(tmp_path) in str(p) for p in service.pipeline.paths.values())
    result = service.read()
    assert not result['complete'] and result['unavailable']
    assert not list(tmp_path.iterdir())


def test_confirmed_owner_supplies_role_and_name(env):
    with env.store.write() as db:
        db.execute("INSERT INTO parties(id,kind,name,created_at,updated_at) VALUES(1,'supplier','虚构已核准工厂',?,?)", (AT, AT))
        db.execute("INSERT INTO identities(party_id,conversation_key,created_at,created_by) VALUES(1,?,?,'fictional')", (env.supplier_key, AT))
    env.inbox.read.return_value['conversations'][env.supplier_key]['counterpart_role'] = 'unclassified'
    result = env.service().read()
    supplier = next(item for item in result['items'] if item['kind'] == 'supplier_chase')
    assert supplier['title'] == '虚构已核准工厂'


def req(**changes):
    state = dict(password_principal=SimpleNamespace(username='fictional'),
        password_recheck=lambda: True, session=SimpleNamespace(authenticated=True))
    state.update(changes)
    return SimpleNamespace(state=SimpleNamespace(**state), query_params={})


def route(service):
    gate = SimpleNamespace(error_response=lambda request, code: JSONResponse({'error': '无权查看'}, status_code=code))
    return v3_ops_queue_api.build(gate, service=service)[0]


def test_api_auth_headers_and_contract(env):
    response = asyncio.run(route(env.service()).endpoint(req()))
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert json.loads(response.body)['total'] == 7


@pytest.mark.parametrize('changes', [dict(password_principal=None), dict(password_recheck=None), dict(session=None)])
def test_auth_before_source(changes):
    service = SimpleNamespace(read=Mock())
    assert asyncio.run(route(service).endpoint(req(**changes))).status_code == 403
    service.read.assert_not_called()


def test_revoked_query_and_sanitised_failure():
    service = SimpleNamespace(read=Mock(return_value={}))
    assert asyncio.run(route(service).endpoint(req(password_recheck=lambda: False))).status_code == 403
    service.read.reset_mock()
    request = req()
    request.query_params = {'path': '/fictional-forbidden'}
    assert asyncio.run(route(service).endpoint(request)).status_code == 400
    service.read.assert_not_called()
    service.read.side_effect = RuntimeError('private-canary')
    response = asyncio.run(route(service).endpoint(req()))
    assert response.status_code == 503 and b'private-canary' not in response.body


def test_readonly_http_methods(env):
    mounted = route(env.service())
    assert mounted.path == '/api/v1/ops/queue' and mounted.methods == {'GET', 'HEAD'}
    app = Starlette(routes=[mounted])
    async def dispatch(method):
        messages = []
        async def receive():
            return dict(type='http.request', body=b'', more_body=False)
        async def send(message):
            messages.append(message)
        await app(dict(type='http', asgi={'version': '3.0'}, http_version='1.1', method=method,
            scheme='http', path=mounted.path, raw_path=mounted.path.encode(), query_string=b'',
            root_path='', headers=[], server=('fake', 80), client=('fake', 1), state=vars(req().state)), receive, send)
        return messages[0]['status']
    for method in ['POST', 'PUT', 'PATCH', 'DELETE']:
        assert asyncio.run(dispatch(method)) == 405


def test_local_inbound_and_wechat_use_real_schema_and_remain_readonly(tmp_path):
    from console.ops_queue import inbox as local
    inbound = tmp_path / 'fictional-inbound.sqlite3'
    wx = tmp_path / 'fictional-wechat.sqlite3'
    with sqlite3.connect(inbound) as db:
        db.execute('CREATE TABLE inbound(id INTEGER PRIMARY KEY,payload TEXT)')
        db.execute('INSERT INTO inbound(payload) VALUES(?)', (json.dumps(dict(account='fictional', conversation_id='buyer',
            buyer_name='虚构买家', content='请问虚构灯的尺寸？', role='buyer', sent_at=AT)),))
    with sqlite3.connect(wx) as db:
        db.execute('CREATE TABLE messages(app_id TEXT,payload TEXT)')
        db.execute('CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,scope_id TEXT,business_role TEXT)')
        db.execute('INSERT INTO messages VALUES(?,?)', ('fictional', json.dumps(dict(group_id='fictional@chatroom',
            message_id='m1', sender_id='fictional-own', own_wxid='fictional-own', text='请给虚构尺寸', created_at=NOW-49*3600))))
        db.execute("INSERT INTO message_scopes VALUES('fictional','m1','scope','supplier')")
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (inbound, wx)]
    buyer = next(iter(local.alibaba(inbound, NOW).values()))
    supplier = next(iter(local.wechat(wx, NOW).values()))
    assert buyer['waiting_reply'] and buyer['counterpart_role'] == 'customer'
    assert not supplier['waiting_reply'] and supplier['counterpart_role'] == 'supplier'
    assert supplier['key'] == make_key('conversation', 'wechat', '微信', 'fictional@chatroom')
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (inbound, wx)]


def test_sqlite_sources_cannot_be_written(env):
    from console.overview.safety import database
    with database(env.path, 1) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("UPDATE deals SET title='cannot change'")


def test_mount_patches_on_temporary_copies(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[2]
    mount = root / 'src/console/ops_queue/mount'
    (tmp_path / 'console').mkdir()
    production = tmp_path / 'console/v3.py'
    production.write_text('def routes(app_module):\n' +
        '    from . import v3_lang_api, v3_pipeline_api\n' +
        '    routes += v3_lang_api.build(app_module)\n' +
        '    routes += v3_pipeline_api.build(app_module)\n' +
        '    from . import v3_alibaba_open_api\n' +
        '    routes += v3_alibaba_open_api.build(app_module, network_enabled=False)\n' +
        '    from . import v3_deal_workflow_api\n')
    (tmp_path / 'scripts').mkdir()
    rehearsal = tmp_path / 'scripts/rehearsal_server.py'
    rehearsal.write_bytes((root / 'scripts/rehearsal_server.py').read_bytes())
    import ast
    for kind, target in [('production', production), ('rehearsal', rehearsal)]:
        before = target.read_bytes()
        patch = mount / (kind + '-mount.patch')
        for options in [('--dry-run',), (), ('-R',)]:
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', *options, '-i', str(patch)],
                cwd=tmp_path, capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
            ast.parse(target.read_text())
        assert target.read_bytes() == before
