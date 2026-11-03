"""Authenticated routes and draft integration, entirely fictional and offline."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from console.kb import Hub
from console.kb.drafting import draft_context
from console.inbox import ai, drafts
from console.inbox.model import make_key
from kb_fixtures import record, request, seed, tm_scope

try:
    from console import v3_kb_api, v3_inbox_api
    from starlette.datastructures import QueryParams, FormData
    from starlette.responses import JSONResponse
except ModuleNotFoundError as exc:
    if exc.name != 'starlette' and not (exc.name or '').startswith('starlette.'):
        raise
    v3_kb_api = v3_inbox_api = None


@unittest.skipIf(v3_kb_api is None, 'starlette not installed')
class ApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.catalog = self.root/'catalog.sqlite3'
        self.hub = Hub(self.catalog, self.root/'brief.md', self.root/'kb.sqlite3', embedder=None)
        self.app = SimpleNamespace(error_response=lambda r, code: JSONResponse({'error':'无权访问'}, status_code=code))
        self.runner = Mock(return_value='虚构回答[1]')
        self.now = 1000.
        self.limiter = v3_kb_api.AskLimiter()
        self.routes = v3_kb_api.build(self.app, hub=self.hub, runner=self.runner, limiter=self.limiter, clock=lambda: self.now)
        self.handlers = {r.name:r.endpoint for r in self.routes}

    def call(self, name='kb_search', **kwargs):
        return asyncio.run(self.handlers[name](request(**kwargs)))

    def data(self, name='kb_search', **kwargs):
        result = self.call(name, **kwargs)
        self.assertEqual(result.status_code, 200, result.body.decode())
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        return json.loads(result.body)

    def test_auth_all_routes_headers_and_post_recheck_before_work(self):
        for name in self.handlers:
            for options in ({'authenticated':False}, {'principal':False}, {'recheck':False}):
                result = self.call(name, query={'q':'灯具'}, **options)
                self.assertEqual(result.status_code, 403)
                self.assertEqual(result.headers['cache-control'], 'no-store')
        for name in ('kb_ask','kb_save_brief'):
            req = request(recheck=False)
            async def body():
                self.fail('POST body read before recheck')
            req.body = body
            with patch.object(self.hub, 'ask', side_effect=AssertionError), \
                    patch.object(self.hub.brief, 'save_brief', side_effect=AssertionError):
                self.assertEqual(asyncio.run(self.handlers[name](req)).status_code, 403)
        self.runner.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_get_recheck_after_read_and_errors(self):
        events = []
        req = request(query={'q':'灯具'})
        req.state.password_recheck = lambda: events.append('check') or True
        def read(**kwargs):
            events.append('read')
            raise RuntimeError('/fictional/private/secret')
        with patch.object(self.hub, 'search', side_effect=read):
            result = asyncio.run(self.handlers['kb_search'](req))
        self.assertEqual(events, ['read','check'])
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('private', result.body.decode())

    def test_missing_database_search_ask_stats_ready_false(self):
        for name, options in [('kb_search',dict(query={'q':'灯具'})), ('kb_stats',{}),
                              ('kb_ask',dict(form={'q':['灯具']}))]:
            self.assertFalse(self.data(name, **options)['ready'])
        self.runner.assert_not_called()
        self.assertFalse(self.catalog.exists())

    def test_parameters_bad_and_duplicates_customer_reply_unavailable(self):
        for query in ({}, {'q':'字'*501}, {'q':'灯','purpose':'customer_reply'}, {'q':'灯','top_k':'21'},
                      {'q':'灯','top_k':'0'}, {'q':'灯','family':'invalid'}, QueryParams('q=灯&q=灯'),
                      QueryParams('q=灯&purpose=owner&purpose=owner')):
            self.assertEqual(self.call(query=query).status_code, 400)
        for fields in ({'q':['灯'],'purpose':['customer_reply']}, {'q':['a','b']}, {'q':['']}):
            self.assertEqual(self.call('kb_ask', form=fields).status_code, 400)
        self.assertEqual(self.call('kb_brief', query={'version':'0'}).status_code, 400)
        self.assertEqual(self.call('kb_brief', query={'version':'100'}).status_code, 404)

    def test_form_cache_parse_qs_versions_and_search(self):
        seed(self.catalog)
        self.assertEqual(len(self.data(query={'q':'外箱','top_k':'2'})['items']), 1)
        self.assertEqual(self.data('kb_save_brief', form=FormData({'text':'虚构背景一'}))['version'], 1)
        self.assertEqual(self.data('kb_save_brief', body=urlencode({'text':'虚构背景二'}).encode())['version'], 2)
        brief = self.data('kb_brief')
        self.assertEqual(brief['text'], '虚构背景二')
        self.assertEqual(brief['versions'][0]['saved_by'], '虚构保存人')
        self.assertNotIn('text', brief['versions'][0])
        self.assertEqual(self.data('kb_brief', query={'version':'1'})['text'], '虚构背景一')
        self.assertEqual(self.call('kb_save_brief', form={'text':['字'*8001]}).status_code, 400)
        self.assertEqual(self.call('kb_save_brief', form={'text':['\x00']}).status_code, 400)
        answer = self.data('kb_ask', body=urlencode({'q':'外箱'}).encode())
        self.assertEqual(answer['answer'], '虚构回答[1]')
        self.runner.side_effect = RuntimeError('fictional-secret')
        failed = self.call('kb_ask', form={'q':['外箱']})
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn('fictional-secret', failed.body.decode())

    def test_minute_hour_limits_and_atomic_reservations(self):
        seed(self.catalog)
        for minute in range(10):
            self.now = 1000 + 60*minute
            for _ in range(6):
                self.assertEqual(self.call('kb_ask', form={'q':['外箱']}).status_code, 200)
            response = self.call('kb_ask', form={'q':['外箱']})
            self.assertEqual(response.status_code, 429)
            self.assertIn('Retry-After', response.headers)
        self.now = 1600
        self.assertEqual(self.call('kb_ask', form={'q':['外箱']}).status_code, 429)
        self.now = 4600
        self.assertEqual(self.call('kb_ask', form={'q':['外箱']}).status_code, 200)
        from concurrent.futures import ThreadPoolExecutor
        limiter = v3_kb_api.AskLimiter()
        with ThreadPoolExecutor(max_workers=12) as pool:
            result = list(pool.map(limiter.reserve, [0.]*30))
        self.assertEqual(result.count(0), 6)


class DraftStorageTests(unittest.TestCase):
    def test_old_schema_read_no_migration_then_write_adds_sources(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'drafts.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(drafts._SCHEMA)
                db.execute('INSERT INTO drafts VALUES(1,?,?,?,?,?,?,?)',
                    ('fictional',1,'fake','中文','虚构正文','[]','new'))
            before = path.read_bytes()
            self.assertEqual(drafts.read_drafts(path)[0]['fictional']['sources'], [])
            self.assertEqual(path.read_bytes(), before)
            sources = [dict(title='虚构说明书', type='供应商资料', date=None)]
            drafts.save_draft(path, 'fictional', dict(text='新虚构正文',language='中文',model='fake',needs_confirmation=[],sources=sources), 2)
            self.assertEqual(drafts.read_drafts(path)[0]['fictional']['sources'], sources)
            with closing(sqlite3.connect(path)) as db:
                self.assertIn('sources_json', {r[1] for r in db.execute('PRAGMA table_info(drafts)')})

    def test_prompt_plain_text_background_sources_and_numeric_rule(self):
        prompt = ai.make_prompt([dict(role='buyer',text='虚构问题')], knowledge='公司背景\n虚构公司\n资料 1（来源：虚构说明书）\n200W')
        for text in ('纯文本，不要 JSON', '公司背景', '来源：虚构说明书', 'needs_confirmation', '事实只能来自对话', '草稿正文不得出现'):
            self.assertIn(text, prompt)
        self.assertIn('公司背景', ai.make_prompt([]))


@unittest.skipIf(v3_inbox_api is None, 'starlette not installed')
class DraftIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.key = make_key('conversation','alibaba','fictional-account','tm:fictional')
        self.conv = dict(key=self.key, channel='alibaba', account='fictional-account', buyer_name='虚构买家', country='',
            first_seen=1,last_seen=2,n_messages=4,source_url=None,channel_label='阿里国际站', counterpart_role='customer',
            dormant=False,can_send=False,copy_hint='虚构提示', messages=[
                dict(role='buyer',text='旧消息不检索',sent_at=1), dict(role='buyer',text='[图片]虚构灯具',sent_at=2),
                dict(role='seller',text='我方消息不检索',sent_at=3), dict(role='buyer',text='外箱尺寸',sent_at=4)])
        self.hub = Hub(self.root/'catalog.sqlite3',self.root/'brief.md',self.root/'kb.sqlite3',embedder=None)
        self.app = SimpleNamespace(error_response=lambda r,c:c)
        self.runner = Mock(return_value='语言：中文\n虚构回复200W 资料 1 [1]\n=====待确认=====\n核对规格')
        self.inbox = SimpleNamespace(detail=lambda key:self.conv)
        self.draft_path = self.root/'drafts.sqlite3'
        self.limit_patch = patch.object(drafts, 'LIMITER', drafts.DraftLimiter())
        self.limit_patch.start()
        self.addCleanup(self.limit_patch.stop)

    def build(self, hub=None, timeout=3):
        return {r.name:r.endpoint for r in v3_inbox_api.build(self.app, self.root/'inbound.sqlite3', self.draft_path,
            runner=self.runner, inbox=self.inbox, hub=hub or self.hub, knowledge_timeout=timeout)}

    def test_safe_prompt_current_history_and_sources_persist_to_detail(self):
        seed(self.hub.index.path, [record(1, text='虚构灯具 外箱尺寸 200W 可公开规格', confidentiality='public'),
            record(2, text='虚构灯具 外箱尺寸 不可泄露成本', cost=1),
            record(3, text='虚构灯具 外箱尺寸 另一个买家的私密信息', kind='icbu_customer_conversation',scope=tm_scope(cid='tm:other')),
            record(4, text='虚构灯具 外箱尺寸 自己会话的历史',kind='icbu_customer_conversation',scope=tm_scope())])
        handlers = self.build()
        with patch.object(self.hub,'context', wraps=self.hub.context) as context:
            result = asyncio.run(handlers['inbox_draft'](request(key=self.key)))
            self.assertEqual(result.status_code, 200, result.body.decode())
            self.assertEqual(context.call_args.args[:2], ('虚构灯具\n外箱尺寸','customer_reply'))
            self.assertEqual(context.call_args.kwargs['conversation_scope'], tm_scope())
        prompt = self.runner.call_args.args[0]
        self.assertIn('公司背景', prompt)
        self.assertIn('来源：', prompt)
        self.assertIn('自己会话的历史', prompt)
        for secret in ('不可泄露成本','另一个买家的私密信息'):
            self.assertNotIn(secret,prompt)
        draft = json.loads(result.body)['draft']
        self.assertEqual(len(draft['sources']), 2)
        self.assertNotIn('资料 1', draft['text'])
        self.assertNotIn('[1]', draft['text'])
        self.assertIn('来源',draft['needs_confirmation'][0])
        detail = asyncio.run(handlers['inbox_detail'](request(key=self.key)))
        self.assertEqual(json.loads(detail.body)['draft']['sources'],draft['sources'])

    def test_supplier_purpose_and_unavailable_timeout_fallback(self):
        seed(self.hub.index.path)
        self.conv['counterpart_role']='supplier'
        with patch.object(self.hub,'context', wraps=self.hub.context) as context:
            result = draft_context(self.hub,self.conv)
            self.assertEqual(context.call_args.args[1],'product_facts')
            self.assertTrue(result['sources'])
        with patch.object(self.hub,'context', side_effect=RuntimeError('/fictional/secret')):
            self.assertEqual(draft_context(self.hub,self.conv)['sources'],[])
        release, entered = threading.Event(), threading.Event()
        def blocked(*args,**kwargs):
            entered.set()
            release.wait(3)
            return dict(text='虚构晚到结果', sources=[])
        try:
            with patch.object(self.hub,'context', side_effect=blocked):
                handlers = self.build(timeout=.02)
                start = time.monotonic()
                result = asyncio.run(handlers['inbox_draft'](request(key=self.key)))
                self.assertLess(time.monotonic()-start,1)
                self.assertEqual(result.status_code,200)
                self.assertEqual(json.loads(result.body)['draft']['sources'],[])
                self.assertIn('公司背景',self.runner.call_args.args[0])
                self.assertNotIn('虚构晚到结果',self.runner.call_args.args[0])
        finally:
            release.set()

    def test_placeholder_only_draft_does_not_search(self):
        self.conv['messages'] = [dict(role='buyer',text='[图片][视频]',sent_at=1)]
        with patch.object(self.hub,'context', side_effect=AssertionError):
            self.assertEqual(draft_context(self.hub,self.conv)['sources'],[])


try:
    import starlette  # noqa: F401
except ModuleNotFoundError:
    starlette = None


@unittest.skipIf(starlette is None, 'starlette not installed')
class WarmupTests(unittest.TestCase):
    def test_injected_hub_never_starts_a_background_load(self):
        from unittest.mock import MagicMock
        from console import v3_kb_api
        hub = MagicMock()
        before = threading.active_count()
        v3_kb_api.build(SimpleNamespace(error_response=lambda *a, **k: 403), hub=hub)
        self.assertEqual(threading.active_count(), before)
        hub.index.snapshot.assert_not_called()

    def test_default_hub_warms_in_a_daemon_thread(self):
        from unittest.mock import MagicMock
        from console import v3_kb_api
        hub = MagicMock()
        with patch.object(v3_kb_api, 'default_hub', return_value=hub):
            v3_kb_api.build(SimpleNamespace(error_response=lambda *a, **k: 403))
        for _ in range(50):
            if hub.index.snapshot.called:
                break
            time.sleep(0.05)
        hub.index.snapshot.assert_called_once()
