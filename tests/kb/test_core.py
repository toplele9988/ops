from collections import Counter
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from console.kb import Hub, default_hub
from console.kb.brief import Brief, DEFAULT_BRIEF
from console.kb.hub import ASK_ERROR, NO_MATCH
from console.kb.index import Index, numpy_module
from console.kb.policy import FAMILIES, access_flags, allowed, classify
from console.kb.search import Search, default_embedder, rrf
from console.kb.text import snippet, tokens
from console.inbox.store import read_database
from kb_fixtures import record, seed, tm_scope, vector


class FixtureTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'catalog.sqlite3'
        self.index = Index(self.path)
        self.hub = Hub(index=self.index, brief_path=self.root/'brief.md', history_path=self.root/'kb.sqlite3', embedder=None)


class PolicyTests(unittest.TestCase):
    def test_classification_table_and_provenance_precedence(self):
        cases = [
            ('supplier', '', '', 'supplier'), ('other', 'internal_supplier', '', 'supplier'),
            ('supplier_image', '', '', 'supplier'), ('public_technical_reference', '', '', 'technical'),
            ('other', 'internal_technical_reference', '', 'technical'),
            ('supplier_chat_candidate', '', '', 'supplier_chat'),
            ('business_supplier_message', '', '', 'supplier_chat'),
            ('other', 'group:fictional', 'CHANNEL_ROLE_SUPPLIER', 'supplier_chat'),
            ('other', 'group:fictional', 'CHANNEL_ROLE_CUSTOMER', 'customer_conversation'),
            ('supplier', 'icbu-tm:fictional:hash', '', 'customer_conversation'),
            ('other', 'icbu-analysis:fictional', '', 'customer_conversation'),
            ('other', 'icbu-inquiry:fictional', '', 'customer_conversation'),
            ('whatsapp_business_conversation', '', '', 'customer_conversation'),
            ('icbu_pwa_customer_conversation', '', '', 'customer_conversation'),
            ('other', 'group:fictional', '', 'group_chat'),
            ('owner_private_video_local_asr_ocr', '', '', 'private_note'),
            ('owner_private_video_local_asr_ocr', 'internal_supplier', '', 'private_note'),
            ('legacy_sealed_business_record', 'internal_supplier', '', 'private_note'),
            ('other', 'private:fictional', '', 'private_note'),
            ('legacy_sealed_business_record', '', '', 'private_note'), ('mystery', '', '', 'other')]
        for kind, scope, role, family in cases:
            with self.subTest(kind=kind, scope=scope):
                self.assertEqual(classify(kind, scope, role), family)

    def test_purpose_table_all_families_and_flags(self):
        for family in FAMILIES:
            for confidential, cost in ((False, False), (True, False), (False, True), (True, True)):
                options = dict(confidential=confidential, is_cost_price=cost, source_scope='own')
                self.assertTrue(allowed(family, 'owner', **options))
                self.assertEqual(allowed(family, 'product_facts', **options), family in ('supplier', 'technical', 'supplier_chat'))
                public = not confidential and not cost
                self.assertEqual(allowed(family, 'customer_reply', **options), public and family in ('supplier', 'technical'))
                self.assertEqual(allowed(family, 'customer_reply', conversation_scope='own', **options),
                    public and family in ('supplier', 'technical', 'customer_conversation'))
                self.assertEqual(allowed(family, 'customer_reply', conversation_scope='own-other', **options),
                    public and family in ('supplier', 'technical'))
        with self.assertRaises(ValueError):
            allowed('supplier', 'invalid')
        for label in ('owner_only', 'internal', 'unknown', ''):
            self.assertEqual(access_flags([(label, 0)]), (True, False))
        self.assertEqual(access_flags([('public', 1)]), (False, True))


class IndexTests(FixtureTest):
    def test_lazy_readonly_no_full_text_in_memory_and_metadata(self):
        seed(self.path, [record(1, text='虚构开头'+'字'*1400, title='/fictional/private/说明书.pdf', date='', mtime=1800000000000000000),
            record(2, scope='group:fictional', kind='business_customer_inquiry', role='CHANNEL_ROLE_CUSTOMER', name='虚构客户群')])
        original, modified = self.path.read_bytes(), self.path.stat().st_mtime_ns
        self.assertIsNone(self.index._snapshot)
        with read_database(self.path) as db:
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM knowledge_chunks')
        snap = self.index.snapshot()
        self.assertEqual(len(snap.chunks[0].text), 1200)
        self.assertGreater(len(self.index.full_text('chunk-1')), 1200)
        self.assertEqual(snap.chunks[0].title, '说明书.pdf')
        self.assertTrue(snap.chunks[0].date.startswith('2027-'))
        self.assertEqual(snap.chunks[1].title, '虚构客户群')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.path.stat().st_mtime_ns, modified)
        self.assertEqual(snap.with_vectors, 2)
        if snap.vectors is not None:
            self.assertEqual(str(snap.vectors.dtype), 'float16')
            self.assertFalse(snap.vectors.flags.writeable)

    def test_change_reload_throttle_and_existing_reader_not_blocked(self):
        seed(self.path)
        now = [0.]
        index = Index(self.path, clock=lambda: now[0])
        first = index.snapshot()
        seed(self.path, [record(2)])
        now[0] = 119.99
        self.assertIs(index.snapshot(), first)
        now[0] = 120
        entered, release = threading.Event(), threading.Event()
        load = index._load
        def slow():
            entered.set()
            release.wait(3)
            return load()
        with patch.object(index, '_load', side_effect=slow):
            thread = threading.Thread(target=index.snapshot)
            thread.start()
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            self.assertIs(index.snapshot(), first)
            self.assertLess(time.monotonic()-started, .1)
            release.set()
            thread.join(3)
        self.assertEqual(len(index.snapshot().chunks), 2)
        self.assertEqual(len(first.chunks), 1)
        self.path.unlink()
        self.assertIsNone(index.snapshot())

    def test_mtime_size_wal_and_numpy_absence(self):
        seed(self.path)
        now = [0.]
        index = Index(self.path, clock=lambda: now[0], use_numpy=False)
        first = index.snapshot()
        self.assertIsNone(first.vectors)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                db.execute("UPDATE knowledge_chunks SET text='虚构更新'")
            now[0] = 120
            self.assertNotEqual(index.snapshot().chunks[0].text, first.chunks[0].text)
        stamp = self.path.stat()
        seed(self.path, [record(2, text='虚构'*20000)])
        os.utime(self.path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        now[0] = 240
        self.assertEqual(len(index.snapshot().chunks), 2)
        with patch('console.kb.index.numpy_module', return_value=None):
            self.assertIsNone(Index(self.path).snapshot().vectors)

    def test_twenty_thousand_fictional_chunks_under_ten_seconds(self):
        seed(self.path, (record(i, aid='asset-'+str(i//20), parent=i//20, child=i%20,
            text=('虚构灯具 参数说明 carton gross weight technical sample '+str(i)+' ')*12) for i in range(20000)))
        started = time.perf_counter()
        snapshot = self.index.snapshot()
        elapsed = time.perf_counter()-started
        self.assertEqual(len(snapshot.chunks), 20000)
        self.assertLess(elapsed, 10)
        if snapshot.vectors is not None:
            self.assertEqual(snapshot.vectors.nbytes, 20000*1024*2)


class SearchTests(FixtureTest):
    def test_lazy_embedder_exact_local_protocol_and_revision(self):
        result = SimpleNamespace(dense=vector(), model_version='BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181')
        engine = Mock()
        engine.encode.return_value = [SimpleNamespace(validated=lambda: result)]
        factory = Mock(return_value=engine)
        with patch.dict('sys.modules', {'video_knowledge':SimpleNamespace(),
                'video_knowledge.engine_client':SimpleNamespace(engine_for=factory)}):
            self.assertEqual(default_embedder('虚构问题'), vector())
            engine.encode.assert_called_once_with(['虚构问题'])
            self.assertEqual(factory.call_args.kwargs['socket_path'], '/run/imgw-bge/bge.sock')
            self.assertEqual(factory.call_args.kwargs['embedding_revision'], result.model_version.split('@')[1])
            result.model_version = 'fictional-wrong-revision'
            with self.assertRaises(ValueError):
                default_embedder('虚构问题')

    def test_lexical_chinese_western_rrf_and_snippet_sanitization(self):
        self.assertEqual(list(tokens('外箱尺寸 LED 200W')), ['外箱', '箱尺', '尺寸', 'led', '200w'])
        seed(self.path, [record(1), record(2, text='虚构 unrelated voltage')])
        for query in ('外箱尺寸', 'GROSS WEIGHT', '200W'):
            found = self.hub.search(query)
            self.assertEqual(found['mode'], 'lexical')
            self.assertEqual(len(found['items']), 1)
        self.assertEqual(rrf([1,2],[2,1])[1], rrf([1,2],[2,1])[2])
        self.assertGreater(rrf([1],[1,2])[1], rrf([1],[1,2])[2])
        text = '前'*700+'命中外箱尺寸 /fictional/private/secret.pdf '+ 'a'*64 + ' C:\\private\\secret.txt fictional/secret.pdf '
        value = snippet(text, '外箱尺寸')
        self.assertLessEqual(len(value), 300)
        self.assertIn('外箱尺寸', value)
        self.assertNotIn('/fictional', value)
        self.assertNotIn('a'*64, value)
        self.assertNotIn('C:\\', value)
        self.assertNotIn('fictional/secret.pdf', value)

    def test_cross_scope_hash_labels_and_customer_isolation(self):
        seed(self.path, [record(1, text='虚构灯具 public', confidentiality='public'),
            record(2, text='虚构灯具 成本', cost=1), record(3, text='虚构灯具 保密', confidentiality='owner_only'),
            record(4, text='虚构灯具 自己历史', kind='icbu_customer_conversation', scope=tm_scope()),
            record(5, text='虚构灯具 别人历史', kind='icbu_customer_conversation', scope=tm_scope(cid='tm:other')),
            record(6, text='虚构灯具 跨范围保密', confidentiality='owner_only', label_scope='group:fictional-other'),
            record(7, kind='supplier_chat_candidate', scope='internal_supplier_chat', text='虚构灯具 供应商沟通')])
        reply = self.hub.search('虚构灯具', 'customer_reply', 20, conversation_scope=tm_scope())
        text = str(reply)
        self.assertIn('自己历史', text)
        for secret in ('成本', '保密', '别人历史', '供应商沟通'):
            self.assertNotIn(secret, text)
        self.assertEqual(len(reply['items']), 2)
        owner = self.hub.search('虚构灯具', 'owner', 20)['items']
        self.assertTrue(any(r['is_cost_price'] for r in owner))
        self.assertTrue(any(r['confidential'] for r in owner))
        self.assertEqual(self.index.conversation_scope('alibaba','fictional-account','tm:fictional'), tm_scope())
        self.assertIsNone(self.index.conversation_scope('wechat','fictional-account','tm:fictional'))
        self.assertIsNone(self.index.conversation_scope('alibaba','wrong-account','tm:fictional'))

    def test_ambiguous_binding_and_scope_prefix_do_not_admit_history(self):
        own = tm_scope()
        seed(self.path,[record(1,kind='icbu_customer_conversation',scope=own),
                        record(2,kind='icbu_customer_conversation',scope=own.replace('fictional-binding','second-binding'))])
        self.assertIsNone(self.index.conversation_scope('alibaba','fictional-account','tm:fictional'))
        self.assertEqual(self.hub.search('外箱','customer_reply',conversation_scope=own+'extra')['items'],[])

    def test_family_quotas_adjacent_merge_asset_cap_and_no_hidden_neighbors(self):
        rows = [record(i, aid='repeated', child=i, text='虚构频闪灯 '+str(i)) for i in range(12)]
        rows += [record(20, kind='public_technical_reference', scope='internal_technical_reference', text='虚构频闪灯 技术'),
                 record(21, kind='icbu_customer_conversation', scope=tm_scope(), text='虚构频闪灯 会话')]
        seed(self.path, rows)
        result = self.hub.search('频闪灯', 'owner', 8)['items']
        counts = Counter(r['family'] for r in result)
        self.assertEqual(counts, {'supplier':2,'technical':1,'customer_conversation':1})
        self.assertIn('\n', next(r['snippet'] for r in result if r['family']=='supplier'))
        self.assertEqual(len({r['source_id'] for r in result}), len(result))
        self.assertEqual([r['rank'] for r in result], list(range(1, len(result)+1)))
        self.assertTrue(all(len(r['snippet'])<=300 for r in result))

    @unittest.skipIf(numpy_module() is None, 'numpy not installed')
    def test_hybrid_semantic_and_fallback_when_embedder_fails_or_missing(self):
        seed(self.path, [record(1, vector=vector(1)), record(2, text='虚构语义资料', vector=vector())])
        search = Search(self.index, lambda q: vector())
        result = search.search('无词法匹配')
        self.assertEqual(result['mode'], 'hybrid')
        self.assertEqual(len(result['items']), 1)
        for embed in (None, Mock(side_effect=RuntimeError('fictional secret')), lambda q: [float('nan')]*1024, lambda q: [0]):
            self.assertEqual(Search(self.index, embed).search('外箱')['mode'], 'lexical')
        with patch.dict('sys.modules', {'video_knowledge.engine_client': None}):
            self.assertEqual(Search(self.index, default_embedder).search('外箱')['mode'], 'lexical')

    def test_search_parameters_and_family_filter(self):
        seed(self.path)
        self.assertEqual(self.hub.search('灯具', families=['technical'])['items'], [])
        for args in [('', 'owner', 8), ('字'*501, 'owner', 8), ('灯','bad',8), ('灯','owner',0), ('灯','owner',21)]:
            with self.assertRaises(ValueError):
                self.hub.search(*args)
        with self.assertRaises(ValueError):
            self.hub.search('灯', families=['unknown'])


class BriefHubTests(FixtureTest):
    def test_brief_versions_permissions_length_controls_and_read_no_creation(self):
        brief = self.hub.brief
        self.assertEqual(brief.read(), DEFAULT_BRIEF)
        self.assertEqual(brief.versions(), [])
        self.assertFalse(brief.path.exists())
        for i in range(23):
            self.assertEqual(brief.save_brief('虚构背景'+str(i)+'\x00\x1b\u202e', '虚构人\x00'), i+1)
        self.assertEqual(brief.read(), '虚构背景22')
        self.assertEqual(brief.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(brief.history_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(brief.versions(30)), 20)
        self.assertEqual(len(brief.versions()), 5)
        self.assertNotIn('text', brief.versions()[0])
        self.assertIsNone(brief.version(1))
        self.assertEqual(brief.version(23)['text'], '虚构背景22')
        for value in ('字'*8001, '', '\x00'):
            with self.assertRaises(ValueError):
                brief.save_brief(value, '虚构人')

    def test_context_budget_empty_and_sources_follow_actual_inclusion(self):
        seed(self.path, [record(i) for i in range(8)])
        self.hub.brief.save_brief('虚构背景'*2000, '虚构人')
        for budget in (1, 30, 100, 500, 3000):
            context = self.hub.context('外箱', 'owner', budget_chars=budget)
            self.assertLessEqual(len(context['text']), budget)
            for s in context['sources']:
                self.assertIn('资料 '+str(s['n']), context['text'])
        empty = self.hub.context('完全不相关的假词', 'owner')
        self.assertIn(NO_MATCH, empty['text'])
        self.assertIn('公司背景', empty['text'])
        self.assertEqual(empty['sources'], [])
        with self.assertRaises(ValueError):
            self.hub.context('外箱', 'owner', budget_chars=0)

    def test_ask_no_match_no_runner_valid_citations_and_safe_failure(self):
        runner = Mock(return_value='虚构回答 [1] [0] [99]')
        result = self.hub.ask('外箱', 'owner', runner)
        self.assertEqual(result['answer'], NO_MATCH)
        runner.assert_not_called()
        seed(self.path)
        result = self.hub.ask('外箱', 'owner', runner)
        self.assertIn('[1]', result['answer'])
        self.assertNotIn('[99]', result['answer'])
        self.assertNotIn('[0]', result['answer'])
        self.assertIn('纯文本', runner.call_args.args[0])
        self.assertIn('资料没有写', runner.call_args.args[0])
        for output in ('', json.dumps({'answer':''}), 23):
            self.assertEqual(self.hub.ask('外箱', runner=lambda p: output)['answer'], ASK_ERROR)
        self.assertEqual(self.hub.ask('外箱', runner=Mock(side_effect=RuntimeError('/fictional/private')))['answer'], ASK_ERROR)
        with patch('console.inbox.ai.official_runner', return_value='虚构默认回答[1]') as official:
            self.assertEqual(self.hub.ask('外箱')['answer'],'虚构默认回答[1]')
            official.assert_called_once()

    def test_stats_missing_and_singleton_no_io(self):
        self.assertFalse(self.hub.stats()['ready'])
        with patch('console.kb.hub._DEFAULT', None), patch.object(Index, '_load', side_effect=AssertionError):
            self.assertIs(default_hub(), default_hub())
        seed(self.path)
        with patch.object(self.index, '_load', side_effect=AssertionError('stats must stay lazy')):
            stats = self.hub.stats()
        self.assertIsNone(stats['loaded_at'])
        self.assertEqual(stats['chunks'], 1)
        self.assertEqual(stats['assets_by_type'], [{'label':'供应商资料','count':1}])


class UnlabeledMaterialTests(unittest.TestCase):
    """Real-data finding: a quote PDF and a proforma invoice carried no label and were released to customer replies."""

    def test_unlabeled_supplier_material_is_confidential_but_public_references_are_not(self):
        from console.kb.policy import access_flags, allowed
        self.assertEqual(access_flags([], 'supplier', 'supplier')[0], True)
        self.assertEqual(access_flags([], 'supplier_image', 'other')[0], True)
        self.assertEqual(access_flags([], 'public_technical_reference', 'technical'), (False, False))
        self.assertEqual(access_flags([('public', 0)], 'supplier', 'supplier'), (False, False))
        confidential, cost = access_flags([], 'supplier', 'supplier')
        self.assertFalse(allowed('supplier', 'customer_reply', confidential=confidential, is_cost_price=cost))
        self.assertTrue(allowed('supplier', 'owner', confidential=confidential, is_cost_price=cost))


class ReadableTitleTests(unittest.TestCase):
    def test_opaque_ids_fall_back_to_the_family_name_but_real_names_stay(self):
        from console.kb.text import title
        for opaque in ('f3c6017c-e9fa-49e4-85cd-754748abcdef', 'f3c6017c-e9fa-49e4-85cd-754748abcdef.txt', 'a' * 32):
            self.assertEqual(title(opaque, '买家会话'), '买家会话')
        self.assertEqual(title('/fictional/dir/中文报价单.pdf', 'x'), '中文报价单.pdf')
        self.assertEqual(title('9417284m0.md', 'x'), '9417284m0.md')
