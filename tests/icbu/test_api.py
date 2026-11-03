"""ICBU review API contracts. All operations and read-only sources are fake."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from rehearsal_isolation import isolate_builders

import asyncio
from contextlib import ExitStack
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from starlette.datastructures import QueryParams
from starlette.responses import JSONResponse

from console import v3_icbu_api
from console.inbox.model import make_key
from console.icbu.adapter import ICBUAdapter, _read_commands, _read_configuration


DRAFT_ID = '11111111-1111-4111-8111-111111111111'
DRAFT_TEXT = 'Hello, please share your target quantity.'
SECRET = 'fictional-config-secret'


class FakeOperations:
    def __init__(self):
        self.enqueue_calls = []
        self.configure_calls = []
        self.events = []

    def event(self, connection, kind, command=None, detail=None):
        self.events.append({'kind': kind, 'command': command, 'detail': detail})

    def enqueue(self, *args, **kwargs):
        self.enqueue_calls.append((args, kwargs))
        return {'id': 'fictional-command-id', 'state': 'queued'}

    def configure(self, *args, **kwargs):
        self.configure_calls.append((args, kwargs))
        self.event(None, 'mode_changed', detail={
            'mode': args[0], 'conversations': args[1], 'config_sha256': SECRET,
        })
        return {'mode': args[0], 'conversations': args[1], 'config_sha256': SECRET}


class FakeReadonly:
    def __init__(self):
        self.state = {
            'drafts': [{
                'id': DRAFT_ID, 'state': 'draft_ready', 'account': 'Amy',
                'buyer_name': '虚构买家 Alice', 'country': 'Fictionland',
                'created_at': '2026-10-29 10:00:00 UTC', 'ready_at': '2026-10-29 10:01:00 UTC',
            }],
            'commands': [],
        }
        self.read_calls = []

    def draft_list(self, page):
        self.read_calls.append(('draft_list', page))
        return {'items': list(self.state['drafts']), 'states': {'draft_ready': 1}, 'total': 1,
                'page': page, 'has_next': False, 'lane_ready': True}

    def draft_detail(self, identifier):
        self.read_calls.append(('draft_detail', identifier))
        if identifier != DRAFT_ID:
            return None
        return {
            'id': DRAFT_ID, 'state': 'draft_ready', 'created_at': '2026-10-29 10:00:00 UTC',
            'ready_at': '2026-10-29 10:01:00 UTC', 'provider': 'fictional-provider',
            'model': 'fictional-model', 'account': 'Amy', 'buyer_name': '虚构买家 Alice',
            'country': 'Fictionland', 'content': 'Fictional buyer asks for a quote.',
            'draft': DRAFT_TEXT, 'conversation_id': 'tm:fictional-conversation',
            'role': 'buyer', 'history': False, 'auto_send': False, 'needs_review': True,
            'sources': [{'title': 'fictional source', 'type': 'faq', 'date': '2026-10-01'}],
            'review_notes': ['请人工核对虚构交期'],
        }

    def overview(self, owner):
        self.read_calls.append(('overview', owner))
        return {
            'observed_at': '2026-10-30 10:00:00 UTC',
            'drafts': {'ok': True, 'data': {'states': {'draft_ready': 1}, 'total': 1, 'lane_ready': True}},
            'probe': {'ok': True, 'data': {'present': True, 'freshness': 'online', 'state': 'running',
                'age_seconds': 4, 'counters': {'contacts_scanned': 3, 'contacts_loaded': 3, 'queued': 1,
                    'blocked': 0, 'unbound': 0, 'metadata_skipped': 0}, 'cycle_complete': True,
                'instance_id': SECRET}},
            'private_config': SECRET,
        }

    def validation(self):
        return {'synthetic': True, 'never_send': True, 'created_at': 1793350000,
                'metadata': {'actual_model': 'fictional-model'},
                'sources': [{'title': 'fictional reference'}],
                'result': {'draft': 'Fictional validation draft.', 'review_notes': ['未发送']}}


class FakeCommandReader:
    def __init__(self, readonly):
        self.readonly = readonly

    def __call__(self, identifier):
        return [dict(item) for item in self.readonly.state['commands'] if item.get('inbound_id') == identifier]


def request(*, fields=None, body=None, query=None, identifier=None, principal=True,
            authenticated=True, recheck=True, headers=None, proxy_trusted=False):
    if fields is not None:
        form = {key: [value] if isinstance(value, str) else value for key, value in fields.items()}
        raw = urlencode(fields).encode('utf-8')
    else:
        form = None
        raw = body or b''

    async def read_body():
        return raw

    headers = dict(headers or {})
    headers.setdefault('content-length', str(len(raw)))
    headers.setdefault('content-type', 'application/x-www-form-urlencoded')
    return SimpleNamespace(
        state=SimpleNamespace(
            password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
            session=SimpleNamespace(authenticated=authenticated),
            password_recheck=lambda: recheck,
            form=form,
            proxy_trusted=proxy_trusted,
        ),
        path_params={'identifier': identifier} if identifier is not None else {},
        query_params=query or {}, headers=headers, body=read_body, _body=raw,
    )


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.operations = FakeOperations()
        self.readonly = FakeReadonly()
        self.app = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '需要登录'}, status_code=code))
        self.adapter = ICBUAdapter(
            operations=self.operations, readonly=self.readonly,
            command_reader=FakeCommandReader(self.readonly),
            command_counts_reader=lambda: {
                'total': 2, 'states': {'queued': 1, 'sent': 1}, 'other': 0, 'clients_seen': 1,
                'client_id': SECRET,
            },
            settings_reader=lambda: {'mode': 'review', 'conversations': [], 'policy': SECRET},
        )
        self.routes = v3_icbu_api.build(self.app, service=self.adapter,
                                        owner_resolver=lambda: 'fictional-owner')
        self.handlers = {route.name: route.endpoint for route in self.routes}

    def call(self, name, **kwargs):
        return asyncio.run(self.handlers[name](request(**kwargs)))

    def data(self, name, **kwargs):
        result = self.call(name, **kwargs)
        self.assertEqual(result.status_code, 200, result.body.decode())
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        return json.loads(result.body)

    def test_routes_methods_and_authentication_recheck(self):
        expected = {
            'icbu_drafts': {'GET'}, 'icbu_draft_detail': {'GET'}, 'icbu_draft_command': {'POST'},
            'icbu_settings': {'GET'}, 'icbu_settings_update': {'POST'},
            'icbu_status': {'GET'}, 'icbu_validation': {'GET'},
        }
        self.assertEqual({route.name: route.methods - {'HEAD'} for route in self.routes}, expected)
        for route in self.routes:
            for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
                result = asyncio.run(route.endpoint(request(identifier=DRAFT_ID, fields={}, **denied)))
                self.assertEqual(result.status_code, 403)
                self.assertEqual(result.headers['cache-control'], 'no-store')
                self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(self.operations.enqueue_calls, [])
        self.assertEqual(self.operations.configure_calls, [])

    def test_post_login_and_recheck_happen_before_body_or_writes(self):
        denied = self.call('icbu_draft_command', identifier=DRAFT_ID, fields={
            'action': 'send', 'draft_sha': 'x', 'text': 'x',
        }, authenticated=False)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(json.loads(denied.body), {
            'error': '需要登录控制台后操作', 'code': 'operation_requires_console_login',
        })
        for name in ('icbu_draft_command', 'icbu_settings_update'):
            req = request(recheck=False, identifier=DRAFT_ID)
            async def forbidden_body():
                self.fail('POST read body before recheck')
            req.body = forbidden_body
            self.assertEqual(asyncio.run(self.handlers[name](req)).status_code, 403)
        self.assertEqual(self.operations.enqueue_calls, [])
        self.assertEqual(self.operations.configure_calls, [])

    def test_list_detail_and_status_are_read_only_and_link_to_new_inbox_key(self):
        before = json.dumps(self.readonly.state, sort_keys=True)
        listing = self.data('icbu_drafts', query={'page': '1'})
        item = listing['items'][0]
        key = make_key('conversation', 'alibaba', 'Amy', 'tm:fictional-conversation')
        self.assertEqual(item['conversation_key'], key)
        self.assertEqual(item['state'], 'draft_ready')
        self.assertFalse(item['approved'])
        self.assertFalse(item['blocked'])
        self.assertEqual(item['updated_at'], '2026-10-29 10:01:00 UTC')
        detail = self.data('icbu_draft_detail', identifier=DRAFT_ID)
        self.assertEqual(detail['draft'], DRAFT_TEXT)
        self.assertEqual(detail['draft_sha'], __import__('hashlib').sha256(DRAFT_TEXT.encode()).hexdigest())
        self.assertEqual(detail['conversation_key'], key)
        self.assertEqual(detail['review_notes'], ['请人工核对虚构交期'])
        self.assertEqual(detail['commands'], [])
        status = self.data('icbu_status')
        self.assertEqual(self.data('icbu_settings'), {'mode': 'review', 'conversations': []})
        self.assertEqual(self.data('icbu_validation')['id'], 'synthetic-validation')
        self.assertEqual(status['executor']['states']['queued'], 1)
        self.assertEqual(status['executor']['clients_seen'], 1)
        self.assertEqual(status['win10']['counters']['contacts_loaded'], 3)
        self.assertNotIn(SECRET, json.dumps(status))
        self.assertEqual(json.dumps(self.readonly.state, sort_keys=True), before)
        self.assertEqual(self.operations.enqueue_calls, [])
        self.assertEqual(self.operations.configure_calls, [])

    def test_status_reuses_legacy_inquiry_owner_check_without_returning_config(self):
        config_path = '/fictional/console-config.json'
        app = SimpleNamespace(
            data=SimpleNamespace(CONFIG=config_path),
            read_json_mapping=lambda path: {'inquiry_owner': 'fictional-owner', 'secret': SECRET},
            inquiry_owner=lambda config: config['inquiry_owner'],
            error_response=lambda req, code: JSONResponse({'error': '需要登录'}, status_code=code),
        )
        handlers = {route.name: route.endpoint for route in v3_icbu_api.build(app, service=self.adapter)}
        result = asyncio.run(handlers['icbu_status'](request()))
        self.assertEqual(result.status_code, 200, result.body.decode())
        self.assertIn(('overview', 'fictional-owner'), self.readonly.read_calls)
        self.assertNotIn(SECRET, result.body.decode())

    def test_get_query_validation_rejects_unknown_and_duplicate_fields(self):
        for query in ({'unknown': 'x'}, QueryParams('page=1&page=2')):
            result = self.call('icbu_drafts', query=query)
            self.assertEqual(result.status_code, 400)
            self.assertIn('查询', json.loads(result.body)['error'])
        for name in ('icbu_settings', 'icbu_status', 'icbu_validation'):
            result = self.call(name, query={'page': '1'})
            self.assertEqual(result.status_code, 400)

    def test_sha_mismatch_is_conflict_and_never_enqueues(self):
        result = self.call('icbu_draft_command', identifier=DRAFT_ID, fields={
            'action': 'send', 'draft_sha': '0' * 64, 'text': 'A reviewed fictional reply',
        })
        self.assertEqual(result.status_code, 409)
        self.assertIn('草稿内容已变化', json.loads(result.body)['error'])
        self.assertEqual(self.operations.enqueue_calls, [])

    def test_manual_valid_command_calls_legacy_enqueue_once_with_retry_fence(self):
        result = self.call('icbu_draft_command', identifier=DRAFT_ID, fields={
            'action': 'send',
            'draft_sha': __import__('hashlib').sha256(DRAFT_TEXT.encode()).hexdigest(),
            'text': '  Edited fictional reply.  ',
            'csrf': 'fictional-csrf',
        })
        self.assertEqual(result.status_code, 200, result.body.decode())
        self.assertEqual(json.loads(result.body), {
            'ok': True, 'command': {'id': 'fictional-command-id', 'state': 'queued'},
        })
        self.assertEqual(len(self.operations.enqueue_calls), 1)
        args, kwargs = self.operations.enqueue_calls[0]
        self.assertEqual(args, (DRAFT_ID, 'send', __import__('hashlib').sha256(DRAFT_TEXT.encode()).hexdigest(),
                                '  Edited fictional reply.  '))
        self.assertEqual(kwargs, {'retry_blocked': True})

    def test_command_validation_never_calls_enqueue(self):
        valid_sha = __import__('hashlib').sha256(DRAFT_TEXT.encode()).hexdigest()
        for fields in (
            {'action': 'automatic', 'draft_sha': valid_sha, 'text': 'reply'},
            {'action': 'send', 'draft_sha': valid_sha, 'text': ' '},
            {'action': 'send', 'draft_sha': valid_sha, 'text': 'reply\x00bad'},
            {'action': 'send', 'draft_sha': valid_sha, 'text': 'reply', 'extra': 'value'},
            {'action': 'send', 'draft_sha': valid_sha},
        ):
            result = self.call('icbu_draft_command', identifier=DRAFT_ID, fields=fields)
            self.assertIn(result.status_code, (400, 409))
        self.assertEqual(self.operations.enqueue_calls, [])

    def test_duplicate_and_oversized_fields_are_rejected_before_enqueue(self):
        raw = urlencode({'action': 'send', 'draft_sha': 'x', 'text': 'one'}).encode()
        duplicate = request(identifier=DRAFT_ID, body=raw + b'&action=send')
        result = asyncio.run(self.handlers['icbu_draft_command'](duplicate))
        self.assertEqual(result.status_code, 400)
        oversized = request(identifier=DRAFT_ID, body=b'x=' + b'a' * 8200)
        result = asyncio.run(self.handlers['icbu_draft_command'](oversized))
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.operations.enqueue_calls, [])

    def test_blocked_history_exposes_safe_reason_without_raw_executor_receipt(self):
        raw_receipt = json.dumps({
            'account': 'Amy', 'id': 'fictional-command-id', 'client_id': 'fictional-client',
            'lease_token': SECRET, 'state': 'blocked', 'platform_message_id': '',
            'detail': 'operations_user_active', 'extra_secret': SECRET,
        })
        self.readonly.state['commands'] = [{
            'id': 'fictional-command-id', 'inbound_id': DRAFT_ID, 'action': 'send',
            'origin': 'owner_console', 'state': 'blocked', 'created_at': 1793350000,
            'claimed_at': 1793350001, 'finished_at': 1793350002,
            'result_json': raw_receipt, 'human_pause_retry_available': True,
        }]
        item = self.data('icbu_drafts')['items'][0]
        detail = self.data('icbu_draft_detail', identifier=DRAFT_ID)
        self.assertTrue(item['approved'])
        self.assertTrue(item['blocked'])
        self.assertIn('操作人员仍在操作', item['blocked_reason'])
        self.assertTrue(detail['commands'][0]['human_pause_retry_available'])
        serialized = json.dumps({'item': item, 'detail': detail}, ensure_ascii=False)
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn('result_json', serialized)
        self.assertNotIn('fictional-client', serialized)

    def test_settings_whitelist_and_legacy_modes(self):
        self.assertEqual(self.data('icbu_settings'), {'mode': 'review', 'conversations': []})
        result = self.call('icbu_settings_update', fields={
            'mode': 'test_auto', 'conversations': 'tm:fictional-one\ntm:fictional-two',
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(json.loads(result.body), {'ok': True})
        self.assertEqual(self.operations.configure_calls, [
            (('test_auto', ['tm:fictional-one', 'tm:fictional-two']), {}),
        ])
        self.assertEqual(self.operations.events, [{
            'kind': 'mode_changed', 'command': None,
            'detail': {'change': 'operations_settings_updated'},
        }])
        self.assertNotIn(SECRET, json.dumps(self.operations.events))
        for fields in (
            {'mode': 'automatic', 'conversations': ''},
            {'mode': 'test_auto', 'conversations': ''},
            {'mode': 'auto', 'conversations': 'tm:fictional-one\nnot-a-key'},
            {'mode': 'review', 'conversations': '', 'secret': SECRET},
        ):
            result = self.call('icbu_settings_update', fields=fields)
            self.assertIn(result.status_code, (400, 409))
        self.assertEqual(len(self.operations.configure_calls), 1)

    def test_validation_endpoint_is_synthetic_only_and_never_actionable(self):
        item = self.data('icbu_validation')
        self.assertEqual(item['id'], 'synthetic-validation')
        self.assertIsNone(item['conversation_key'])
        self.assertFalse(item['approved'])
        self.assertIn('未发送', item['buyer_name'])
        self.assertEqual(self.operations.enqueue_calls, [])

    def test_rehearsal_mode_forbids_all_writes_before_form_parsing(self):
        with patch.dict(os.environ, {'IMGW_REHEARSAL': '1'}):
            handlers = {route.name: route.endpoint for route in v3_icbu_api.build(
                self.app, service=self.adapter, owner_resolver=lambda: 'fictional-owner', allow_writes=True)}
            for name in ('icbu_draft_command', 'icbu_settings_update'):
                req = request(identifier=DRAFT_ID)
                async def forbidden_body():
                    self.fail('rehearsal must deny before parsing form')
                req.body = forbidden_body
                result = asyncio.run(handlers[name](req))
                self.assertEqual(result.status_code, 403)
                self.assertEqual(json.loads(result.body)['code'], 'writes_disabled')
        self.assertEqual(self.operations.enqueue_calls, [])
        self.assertEqual(self.operations.configure_calls, [])

    def test_rehearsal_script_auto_mounts_icbu_with_fake_sources_and_writes_disabled(self):
        with tempfile.TemporaryDirectory() as root, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {
                'IMGW_REHEARSAL': '1', 'REAL_ARCHIVE': str(Path(root) / 'missing-fictional.sqlite3')}))
            script = Path(__file__).resolve().parents[2] / 'scripts' / 'rehearsal_server.py'
            spec = importlib.util.spec_from_file_location('icbu_rehearsal_test', script)
            rehearsal = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rehearsal)
            isolate_builders(stack, rehearsal, 'v3_icbu_api')
            original = v3_icbu_api.build
            captured = {}

            def fake_build(gate, **options):
                captured.update(options)
                return original(gate, service=self.adapter, **options)

            stack.enter_context(patch.object(v3_icbu_api, 'build', side_effect=fake_build))
            routes = rehearsal.build_routes(root)
            self.assertFalse(captured['allow_writes'])
            self.assertEqual({route.path for route in routes}, {
                '/api/v1/icbu/drafts', '/api/v1/icbu/drafts/{identifier}',
                '/api/v1/icbu/drafts/{identifier}/command', '/api/v1/icbu/settings',
                '/api/v1/icbu/status', '/api/v1/icbu/validation',
            })
            self.assertFalse((Path(root) / 'archive.sqlite3').exists())

    def test_response_and_exception_text_never_expose_credentials_or_stack(self):
        self.assertNotIn(SECRET, json.dumps(self.data('icbu_settings')))
        self.assertNotIn(SECRET, json.dumps(self.data('icbu_status')))
        failing = ICBUAdapter(operations=self.operations, readonly=self.readonly,
            command_reader=FakeCommandReader(self.readonly),
            settings_reader=lambda: (_ for _ in ()).throw(RuntimeError(SECRET + '/fictional/path')))
        handlers = {route.name: route.endpoint for route in v3_icbu_api.build(
            self.app, service=failing, owner_resolver=lambda: 'fictional-owner')}
        result = asyncio.run(handlers['icbu_settings'](request()))
        self.assertEqual(result.status_code, 503)
        self.assertNotIn(SECRET, result.body.decode())
        self.assertNotIn('/fictional/path', result.body.decode())


class ReadOnlyStoreTests(unittest.TestCase):
    def test_legacy_command_and_settings_projections_use_read_only_connections(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / 'fake-operations.sqlite3'
            policy_path = Path(root) / 'fake-policy.json'
            policy = {'operations_mode': 'review', 'auto_send': False,
                      'auto_send_conversations': [], 'fictional_secret': SECRET}
            policy_path.write_text(json.dumps(policy))
            with sqlite3.connect(db_path) as db:
                db.executescript('''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE commands(id TEXT PRIMARY KEY,inbound_id TEXT,account TEXT,conversation_id TEXT,
                    buyer_name TEXT,action TEXT,text TEXT,text_sha256 TEXT,origin TEXT,state TEXT,created_at REAL,
                    expires_at REAL,client_id TEXT,lease_token TEXT,claimed_at REAL,finished_at REAL,result_json TEXT);''')
                db.execute("INSERT INTO commands(id,inbound_id,account,conversation_id,buyer_name,action,text,text_sha256,origin,state,created_at,expires_at) VALUES('cmd','draft','Amy','tm:a','fictional','send','secret body','hash','owner_console','blocked',1,2)")
            ops = SimpleNamespace(db=db_path, policy=policy_path)
            before = db_path.read_bytes()
            rows = _read_commands(ops, 'draft')
            counts = __import__('console.icbu.adapter', fromlist=['_read_command_counts'])._read_command_counts(ops)
            settings = _read_configuration(ops)
            self.assertEqual(rows[0]['state'], 'blocked')
            self.assertEqual(counts['states']['blocked'], 1)
            self.assertEqual(settings, {'mode': 'review', 'conversations': []})
            self.assertEqual(db_path.read_bytes(), before)
            self.assertNotIn('text', rows[0])
            self.assertNotIn(SECRET, json.dumps(settings))

    def test_retry_blocked_flag_requires_the_exact_legacy_human_pause_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / 'fake-operations.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.executescript('''CREATE TABLE commands(id TEXT PRIMARY KEY,inbound_id TEXT,account TEXT,
                    conversation_id TEXT,buyer_name TEXT,action TEXT,text TEXT,text_sha256 TEXT,origin TEXT,
                    state TEXT,created_at REAL,expires_at REAL,client_id TEXT,lease_token TEXT,claimed_at REAL,
                    finished_at REAL,result_json TEXT);''')
                good = {'account': 'Amy', 'id': 'good', 'client_id': 'client-a', 'lease_token': 'lease-a',
                        'state': 'blocked', 'platform_message_id': '', 'detail': 'operations_user_active'}
                bad = dict(good, id='bad', lease_token='other-lease')
                for identifier, receipt in (('bad', bad), ('good', good)):
                    db.execute('''INSERT INTO commands(id,inbound_id,account,conversation_id,buyer_name,action,text,
                        text_sha256,origin,state,created_at,expires_at,client_id,lease_token,result_json)
                        VALUES(?,?,?,'tm:a','fictional','send','body','sha','owner_console','blocked',1,2,?,?,?)''',
                        (identifier, 'draft', 'Amy', 'client-a', 'lease-a', json.dumps(receipt)))
            ops = SimpleNamespace(db=db_path)
            rows = _read_commands(ops, 'draft')
            availability = {row['id']: row['human_pause_retry_available'] for row in rows}
            self.assertEqual(availability, {'bad': False, 'good': True})


if __name__ == '__main__':
    unittest.main()
