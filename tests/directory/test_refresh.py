"""Synthetic contact replies and a private temporary cache; no network calls."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from console.directory.errors import RefreshFailed, RefreshLimited
from console.directory.refresh import RefreshCache


def reply(friends=None, chatrooms=None, ghs=None):
    return dict(ret=200, data=dict(friends=friends or [], chatrooms=chatrooms or [], ghs=ghs or []))


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'cache' / 'directory.sqlite3'
        self.now = 10000.0
        self.network = patch('socket.socket.connect', side_effect=AssertionError('test must not use network'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def cache(self, fetcher=None, **kwargs):
        return RefreshCache(self.path, clock=lambda: self.now, contact_fetcher=fetcher, **kwargs)

    def test_missing_read_does_not_create_cache_or_directory(self):
        self.assertEqual(self.cache().read(), [])
        self.assertFalse(self.path.parent.exists())

    def test_disabled_does_not_create_cache_or_call_anything(self):
        with self.assertRaises(RefreshFailed) as caught:
            self.cache().refresh()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('只读联系人接口', str(caught.exception))
        self.assertFalse(self.path.parent.exists())

    def test_success_stores_allowlisted_metadata_only_and_sets_private_permissions(self):
        payload = reply(['fiction-person'], ['fiction-room@chatroom'], ['gh_fiction'])
        payload['secret'] = 'synthetic-secret-canary'
        payload['data']['authorization'] = 'synthetic-secret-canary'
        result = self.cache(lambda: payload).refresh()
        self.assertEqual(result['received'], 3)
        self.assertEqual(result['by_kind'], dict(group=1, private=1, official=1))
        rows = self.cache().read()
        self.assertEqual({r['kind'] for r in rows}, {'group', 'private', 'official'})
        self.assertTrue(all(set(r) == {'id', 'kind', 'first_seen', 'last_seen',
                                      'nickname', 'remark', 'name_updated_at'} for r in rows))
        self.assertTrue(all(r['nickname'] is r['remark'] is r['name_updated_at'] is None for r in rows))
        self.assertTrue(all(r['first_seen'] == self.now == r['last_seen'] for r in rows))
        self.assertNotIn('synthetic-secret-canary', self.path.read_bytes().decode('latin1'))
        self.assertNotIn('synthetic-secret-canary', json.dumps(result))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_success_deduplicates_and_preserves_first_seen(self):
        self.cache(lambda: reply(chatrooms=['fiction-a@chatroom', 'fiction-a@chatroom'])).refresh()
        self.now += 600
        result = self.cache(lambda: reply(chatrooms=['fiction-a@chatroom', 'fiction-b@chatroom'])).refresh()
        self.assertEqual(result['received'], 2)
        rows = {r['id']: r for r in self.cache().read()}
        self.assertEqual(rows['fiction-a@chatroom']['first_seen'], 10000)
        self.assertEqual(rows['fiction-a@chatroom']['last_seen'], 10600)
        self.assertEqual(rows['fiction-b@chatroom']['first_seen'], 10600)

    def test_empty_reply_keeps_previously_observed_contacts(self):
        self.cache(lambda: reply(friends=['fiction-person'])).refresh()
        self.now += 600
        result = self.cache(lambda: reply()).refresh()
        self.assertEqual(result['received'], 0)
        self.assertEqual(self.cache().read()[0]['last_seen'], 10000)

    def test_read_does_not_modify_cache_hash_or_mtime(self):
        self.cache(lambda: reply(friends=['fiction-person'])).refresh()
        before = fingerprint(self.path)
        self.cache().read()
        self.assertEqual(fingerprint(self.path), before)

    def test_rate_limit_persists_across_instances_and_exact_boundary(self):
        calls = []
        def fetch():
            calls.append(1)
            return reply()
        self.cache(fetch).refresh()
        self.now += 599
        with self.assertRaises(RefreshLimited) as caught:
            self.cache(fetch).refresh()
        self.assertEqual(caught.exception.retry_after, 1)
        self.assertEqual(len(calls), 1)
        self.now += 1
        self.cache(fetch).refresh()
        self.assertEqual(len(calls), 2)

    def test_concurrent_instances_reserve_before_fetch(self):
        entered, release = threading.Event(), threading.Event()
        def fetch():
            entered.set()
            release.wait(2)
            return reply()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.cache(fetch).refresh)
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(RefreshLimited):
                    self.cache(lambda: self.fail('duplicate fetch')).refresh()
            finally:
                release.set()
            first.result(timeout=2)

    def test_reservations_are_shared_by_separate_processes(self):
        script = '''
import socket, sys
from console.directory.refresh import RefreshCache
from console.directory.errors import RefreshLimited
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('no network'))
try:
    RefreshCache(sys.argv[1], clock=lambda: 10000,
        contact_fetcher=lambda: {'ret':200,'data':{'friends':[],'chatrooms':[],'ghs':[]}}).refresh()
except RefreshLimited:
    sys.exit(29)
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, str(self.path)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            results.append(process.returncode)
            self.assertEqual(stdout, b'')
            self.assertEqual(stderr, b'')
        self.assertEqual(sorted(results), [0, 29, 29])

    def test_failure_is_sanitized_not_retried_and_consumes_limit(self):
        calls = []
        def fetch():
            calls.append(1)
            raise RuntimeError('fictional-private-token-must-not-leak')
        with self.assertRaises(RefreshFailed) as caught:
            self.cache(fetch).refresh()
        self.assertNotIn('fictional-private-token', str(caught.exception))
        self.assertIn('不重试', str(caught.exception))
        with self.assertRaises(RefreshLimited):
            self.cache(fetch).refresh()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.cache().read(), [])

    def test_none_means_unknown_and_preserves_old_contacts(self):
        self.cache(lambda: reply(friends=['fiction-person'])).refresh()
        self.now += 600
        with self.assertRaises(RefreshFailed) as caught:
            self.cache(lambda: None).refresh()
        self.assertIn('结果', str(caught.exception))
        self.assertEqual(len(self.cache().read()), 1)

    def test_timeout_exception_gets_clear_chinese_reason(self):
        def fetch():
            raise TimeoutError('fictional-private-timeout-detail')
        with self.assertRaises(RefreshFailed) as caught:
            self.cache(fetch).refresh()
        self.assertEqual(caught.exception.status_code, 504)
        self.assertIn('超时', str(caught.exception))
        self.assertNotIn('fictional-private', str(caught.exception))

    def test_hard_timeout_does_not_save_late_result_or_retry(self):
        release, finished = threading.Event(), threading.Event()
        calls = []
        def fetch():
            calls.append(1)
            release.wait(2)
            finished.set()
            return reply(friends=['fiction-late'])
        try:
            with self.assertRaises(RefreshFailed) as caught:
                self.cache(fetch, timeout=.03).refresh()
            self.assertEqual(caught.exception.status_code, 504)
            with self.assertRaises(RefreshLimited):
                self.cache(fetch).refresh()
        finally:
            release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(calls, [1])
        self.assertEqual(self.cache().read(), [])

    def test_bad_envelopes_and_invalid_ids_are_rejected_without_partial_save(self):
        payloads = [[], {}, dict(ret=True, data={}), dict(ret=403, data={}), dict(ret=200, data={}),
                    dict(ret=200, data=dict(friends=[], chatrooms=[], ghs='bad')),
                    reply(chatrooms=['fiction-not-a-room']), reply(friends=['fiction-wrong@chatroom']),
                    reply(friends=['fiction-good', {'id': 'bad'}]), reply(friends=['fiction-\n-invalid']),
                    reply(friends=['fiction-\ud800']), reply(friends=['fiction-same'], ghs=['fiction-same'])]
        for payload in payloads:
            with self.subTest(payload=repr(payload)):
                with self.assertRaises(RefreshFailed) as caught:
                    self.cache(lambda: payload).refresh()
                self.assertIn('格式', str(caught.exception))
                self.assertEqual(self.cache().read(), [])
                self.now += 600

    def test_symlink_cache_is_rejected_without_touching_target(self):
        target = Path(self.tmp.name) / 'source.sqlite3'
        target.write_text('synthetic source')
        self.path.parent.mkdir()
        self.path.symlink_to(target)
        before = fingerprint(target)
        with self.assertRaises(RefreshFailed):
            self.cache(lambda: self.fail('must not fetch')).refresh()
        self.assertEqual(fingerprint(target), before)

    def test_symlink_parent_is_rejected(self):
        target = Path(self.tmp.name) / 'target'
        target.mkdir()
        self.path.parent.symlink_to(target, target_is_directory=True)
        with self.assertRaises(RefreshFailed):
            self.cache(lambda: self.fail('must not fetch')).refresh()
        self.assertEqual(list(target.iterdir()), [])

    def test_source_database_cannot_accidentally_be_used_as_cache(self):
        self.path.parent.mkdir()
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE messages (id TEXT)')
            db.execute("INSERT INTO messages VALUES ('fiction-message')")
        before = fingerprint(self.path)
        with self.assertRaises(RefreshFailed):
            self.cache(lambda: self.fail('must not fetch')).refresh()
        self.assertEqual(fingerprint(self.path), before)

    def test_clock_rollback_does_not_bypass_cooldown(self):
        self.cache(lambda: reply()).refresh()
        self.now -= 100
        with self.assertRaises(RefreshLimited) as caught:
            self.cache(lambda: self.fail('must not fetch')).refresh()
        self.assertEqual(caught.exception.retry_after, 700)


if __name__ == '__main__':
    unittest.main()
