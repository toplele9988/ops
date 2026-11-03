"""Full order polling against a synthetic in-process TOP/GOP service."""
import json
import logging
from types import SimpleNamespace

import pytest

from console.archive import validation as v
from console.archive.store import Store as ArchiveStore
from console.alibaba_open.errors import OpenError
from console.alibaba_open.protocol import Client, top_sign, gop_sign
from console.alibaba_open.service import AlibabaOpen
from console.alibaba_open.store import Store

NOW = 2_000_000_000
ACCESS = 'FICTIONAL_ACCESS_SENTINEL_ONLY'
REFRESH = 'FICTIONAL_REFRESH_SENTINEL_ONLY'
SECRET = 'FICTIONAL_APP_SECRET_ONLY'


class FakeCredentials:
    def status(self):
        return dict(configured=True, message='虚构凭据已准备好')

    def load(self):
        return dict(app_key='fictional-app', app_secret=SECRET)


class FakeTOPGOP:
    """A local callable service; no listening port and no sockets."""
    def __init__(self):
        self.calls = []
        self.rows = []
        self.failure = None
        self.refresh_failure = False
        self.code_unknown = False
        self.fail_get = None
        self.amount = '123.45'
        self.currency = 'USD'
        self.status = 'paid'

    def __call__(self, method, url, params, timeout):
        self.calls.append(params.get('method', url.rsplit('/', 1)[-1]))
        assert method == 'POST' and timeout == 10
        if params.get('method'):
            assert params['sign'] == top_sign(params, SECRET)
            assert params['sign_method'] == 'hmac'
            assert params['session'] in (ACCESS, 'FICTIONAL_ACCESS_REFRESHED')
            if self.failure:
                if isinstance(self.failure, Exception):
                    raise self.failure
                return self.failure, {'error_message': ACCESS}
            if params['method'].endswith('.list'):
                query = json.loads(params['param_trade_ecology_order_list_query'])
                page, size = query['start_page'], query['page_size']
                rows = [dict(trade_id=oid, modify_date=dict(timestamp=modified))
                        for oid, modified in self.rows[page * size:(page + 1) * size]]
                return 200, {'alibaba_seller_order_list_response': {'result': {
                    'success': True, 'value': {'total_count': len(self.rows),
                                              'order_list': {'trade_ecology_order': rows}}}}}
            oid = params['e_trade_id']
            if oid == self.fail_get:
                return 503, {'error_message': REFRESH}
            return 200, {'alibaba_seller_order_get_response': {'value': {
                'trade_id': oid, 'total_amount': {'amount': self.amount, 'currency': self.currency},
                'buyer': {'immutable_eid': 'fictional-buyer'}, 'trade_status': self.status}}}
        path = '/auth/token/' + url.rsplit('/', 1)[-1]
        assert params['sign'] == gop_sign(path, params, SECRET)
        assert params['timestamp'] == str(NOW * 1000)
        if self.code_unknown:
            raise TimeoutError(ACCESS + REFRESH)
        if self.refresh_failure:
            return 400, {'error_message': REFRESH}
        return 200, dict(access_token='FICTIONAL_ACCESS_REFRESHED', refresh_token='FICTIONAL_REFRESH_NEW',
                         expires_in=3600, refresh_expires_in=86400)


@pytest.fixture
def env(tmp_path):
    now = [NOW]
    clock = lambda: now[0]
    archive = ArchiveStore(tmp_path / 'archive.sqlite3', target_version=8)
    archive.initialize()
    credentials, fake = FakeCredentials(), FakeTOPGOP()
    client = Client(credentials, transport=fake, clock=clock,
                    top_url='https://fake.invalid/top', gop_url='https://fake.invalid/rest',
                    auth_url='https://fake.invalid/authorize', redirect_uri='https://fake.invalid/return')
    store = Store(tmp_path / 'open.sqlite3', clock=clock)
    store.save_token(dict(access_token=ACCESS, refresh_token=REFRESH,
                          expires_in=3600, refresh_expires_in=86400))
    service = AlibabaOpen(archive.path, store=store, credentials=credentials,
                          client=client, clock=clock, page_size=2)
    service.write('settings', dict(paused='0', confirm='1'), 'fictional-owner')
    return SimpleNamespace(service=service, fake=fake, archive=archive, store=store, now=now)


def poll(env):
    return env.service.write('poll', {'confirm': '1'}, 'fictional-owner')


def test_list_pagination_details_and_repeated_orders_never_auto_win(env):
    env.fake.rows = [('fictional-order-' + str(i), NOW * 1000 - 1000) for i in range(3)]
    assert poll(env)['updated'] == 3
    assert env.fake.calls.count('alibaba.seller.order.list') == 2
    assert env.fake.calls.count('alibaba.seller.order.get') == 3
    assert poll(env)['updated'] == 0
    assert env.fake.calls.count('alibaba.seller.order.get') == 3
    assert env.service._values()['watermark_ms'] == NOW * 1000
    with env.archive.read() as db:
        rows = db.execute('SELECT * FROM deals').fetchall()
        assert len(rows) == 3
        assert all(r['status'] == 'open' and r['won_at'] is None and r['customer_party_id'] is None for r in rows)
        assert db.execute('SELECT count(*) FROM deal_payments').fetchone()[0] == 0


def test_changed_order_gets_new_detail_and_keeps_same_deal(env):
    env.fake.rows = [('fictional-order', NOW * 1000 - 1000)]
    poll(env)
    first = env.service.read('candidates')['items'][0]
    env.fake.rows = [('fictional-order', NOW * 1000)]
    env.fake.amount = '234.56'
    poll(env)
    second = env.service.read('candidates')['items'][0]
    assert first['deal_id'] == second['deal_id']
    assert second['amount'] == '234.56'
    assert second['paid_at_ms'] is None


def test_get_failure_never_advances_watermark_and_resumes_idempotently(env):
    env.service._set(watermark_ms=NOW * 1000 - 60000)
    env.fake.rows = [('fictional-one', NOW * 1000 - 1000), ('fictional-two', NOW * 1000)]
    env.fake.fail_get = 'fictional-two'
    with pytest.raises(OpenError):
        poll(env)
    assert env.service._values()['watermark_ms'] == NOW * 1000 - 60000
    assert len(env.service.read('candidates')['items']) == 1
    env.fake.fail_get = None
    env.now[0] += 31
    assert poll(env)['updated'] == 1
    assert len(env.service.read('candidates')['items']) == 2


@pytest.mark.parametrize('failure', [429, 500, 503, TimeoutError(ACCESS)])
def test_limit_timeout_and_5xx_backoff_without_retries(env, failure):
    env.fake.failure = failure
    with pytest.raises(OpenError):
        poll(env)
    assert len(env.fake.calls) == 1
    assert 'watermark_ms' not in env.service._values()
    assert env.service.read('status')['next_poll_at'] == NOW + 30
    with pytest.raises(v.Conflict):
        poll(env)
    assert len(env.fake.calls) == 1
    env.now[0] += 31
    with pytest.raises(OpenError):
        poll(env)
    assert env.service.read('status')['next_poll_at'] == NOW + 31 + 60


def test_expired_access_token_refreshes_once_before_order_read(env):
    env.store.save_token(dict(access_token=ACCESS, refresh_token=REFRESH, expires_in=1, refresh_expires_in=86400))
    poll(env)
    assert env.fake.calls == ['refresh', 'alibaba.seller.order.list']
    assert env.store.token()['access_token'] == 'FICTIONAL_ACCESS_REFRESHED'
    assert not env.service._values()['refresh_pending']


def test_failed_refresh_requires_new_authorization_and_never_replays(env):
    env.store.save_token(dict(access_token=ACCESS, refresh_token=REFRESH, expires_in=1, refresh_expires_in=86400))
    env.fake.refresh_failure = True
    with pytest.raises(OpenError, match='重新授权'):
        poll(env)
    assert env.fake.calls == ['refresh']
    assert not env.service.read('status')['authorized']
    env.now[0] += 31
    with pytest.raises(OpenError, match='重新'):
        poll(env)
    assert env.fake.calls == ['refresh']


def test_pending_refresh_after_crash_requires_reauthorization(env):
    env.service._set(refresh_pending=True)
    with pytest.raises(OpenError, match='重新'):
        poll(env)
    assert not env.fake.calls
    assert not env.service.read('status')['authorized']


def test_unknown_code_exchange_is_durable_and_not_automatically_replayed(env, caplog):
    env.fake.code_unknown = True
    with caplog.at_level(logging.DEBUG), pytest.raises(OpenError, match='不会自动重试'):
        env.service.write('authorize', dict(code='fictional-once-code', confirm='1'), 'fictional-owner')
    with pytest.raises(v.Conflict):
        env.service.write('authorize', dict(code='fictional-once-code', confirm='1'), 'fictional-owner')
    assert env.fake.calls == ['create']
    assert ACCESS not in caplog.text and REFRESH not in caplog.text and SECRET not in caplog.text
    with env.store.read() as db:
        assert 'fictional-once-code' not in str([tuple(r) for r in db.execute('SELECT * FROM kv')])


def test_daily_budget_covers_list_get_and_token_calls(env):
    env.service.write('settings', dict(daily_limit='1', confirm='1'), 'fictional-owner')
    env.fake.rows = [('fictional-one', NOW * 1000)]
    with pytest.raises(v.Conflict, match='上限'):
        poll(env)
    assert env.fake.calls == ['alibaba.seller.order.list']
    assert env.service.read('status')['calls_today'] == 1
    assert 'watermark_ms' not in env.service._values()
    env.now[0] += 86400
    assert env.service.read('status')['calls_today'] == 0


def test_paused_switch_never_calls_service(env):
    env.service.write('settings', dict(paused='1', confirm='1'), 'fictional-owner')
    with pytest.raises(v.Conflict, match='暂停'):
        poll(env)
    assert env.fake.calls == []


@pytest.mark.parametrize('field,value', [('amount', '-1.00'), ('amount', 'NaN'),
                                        ('currency', 'JPY'), ('currency', 'usd')])
def test_invalid_money_or_currency_rejects_and_preserves_watermark(env, field, value):
    env.fake.rows = [('fictional-one', NOW * 1000)]
    setattr(env.fake, field, value)
    with pytest.raises((OpenError, v.BadRequest)):
        poll(env)
    assert not env.service.read('candidates')['items']
    assert 'watermark_ms' not in env.service._values()


def test_unpaid_does_not_create_deal_or_candidate(env):
    env.fake.rows = [('fictional-one', NOW * 1000)]
    env.fake.status = 'unpay'
    assert poll(env)['updated'] == 0
    assert not env.service.read('candidates')['items']
    with env.archive.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 0


def test_unknown_status_after_payment_only_updates_hint(env):
    env.fake.rows = [('fictional-one', NOW * 1000 - 1000)]
    poll(env)
    env.fake.rows = [('fictional-one', NOW * 1000)]
    env.fake.status = 'closed'
    poll(env)
    assert env.service.read('candidates')['items'][0]['payment_status'] == 'unknown'
    with env.archive.read() as db:
        assert db.execute('SELECT count(*) FROM deal_payments').fetchone()[0] == 0


def test_all_writes_require_confirmation(env):
    with pytest.raises(v.BadRequest, match='确认'):
        env.service.write('poll', {}, 'fictional-owner')
    assert env.fake.calls == []


def test_future_modified_time_rejected_without_progress(env):
    env.fake.rows = [('fictional-one', NOW * 1000 + 1)]
    with pytest.raises(OpenError, match='修改时间'):
        poll(env)
    assert env.fake.calls == ['alibaba.seller.order.list']
    assert 'watermark_ms' not in env.service._values()


def test_status_reads_only_metadata_and_creates_no_files(tmp_path):
    class MissingCredentials:
        def status(self):
            return dict(configured=False, message='未配置，请管理员保存凭据')
        def load(self):
            raise AssertionError('状态页不得读取凭据内容')
    service = AlibabaOpen(tmp_path / 'archive.sqlite3', db_path=tmp_path / 'private.sqlite3',
                          credentials=MissingCredentials(), clock=lambda: NOW)
    status = service.read('status')
    assert not status['configured'] and not status['authorized']
    assert status['paused'] and not status['network_enabled']
    assert '未配置' in status['next_step']
    assert list(tmp_path.iterdir()) == []


def test_status_expired_access_and_refresh_require_authorization(env):
    env.store.save_token(dict(access_token=ACCESS, refresh_token=REFRESH,
                              expires_in=1, refresh_expires_in=1))
    env.now[0] += 2
    assert not env.service.read('status')['authorized']
    with pytest.raises(OpenError, match='重新授权'):
        poll(env)
    assert env.fake.calls == []


def test_candidate_with_missing_deal_is_recovered_despite_observed_cache(env):
    env.fake.rows = [('fictional-one', NOW * 1000)]
    poll(env)
    with env.archive.write() as db:
        db.execute('DELETE FROM deals')
    assert poll(env)['updated'] == 1
    with env.archive.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 1
    assert len(env.service.read('candidates')['items']) == 1


def test_archive_and_private_store_may_never_alias(tmp_path):
    archive = tmp_path / 'archive.sqlite3'
    service_options = dict(credentials=FakeCredentials(), clock=lambda: NOW)
    with pytest.raises(v.BadRequest, match='不同'):
        AlibabaOpen(archive, db_path=archive, **service_options)
    with pytest.raises(v.BadRequest, match='不同'):
        AlibabaOpen(archive, db_path=tmp_path / 'open.sqlite3', key_path=archive, **service_options)
    assert list(tmp_path.iterdir()) == []


def test_default_network_guard_never_opens_a_socket(env):
    guarded = AlibabaOpen(env.archive.path, store=env.store, credentials=FakeCredentials(), clock=lambda: NOW)
    with pytest.raises(OpenError, match='尚未启用'):
        guarded.write('poll', dict(confirm='1'), 'fictional-owner')
    assert guarded.read('status')['calls_today'] == 0


def test_invalid_top_token_is_marked_for_reauthorization(env):
    env.fake.failure = 401
    with pytest.raises(OpenError, match='重新授权'):
        poll(env)
    assert not env.service.read('status')['authorized']
    assert env.store.metadata()['reauthorize']


def test_clock_rollback_never_regresses_watermark(env):
    env.service._set(watermark_ms=NOW * 1000 + 1000)
    with pytest.raises(OpenError, match='校准时钟'):
        poll(env)
    assert not env.fake.calls
    assert env.service._values()['watermark_ms'] == NOW * 1000 + 1000


def test_initial_history_boundary_is_stable_across_failed_days(env):
    env.fake.failure = 503
    with pytest.raises(OpenError):
        poll(env)
    initial = env.service._values()['initial_since_ms']
    env.now[0] += 86400
    with pytest.raises(OpenError):
        poll(env)
    assert env.service._values()['initial_since_ms'] == initial
    assert 'watermark_ms' not in env.service._values()


def test_concurrent_poll_is_rejected_before_any_http_call(env):
    with env.service._operation(), pytest.raises(v.Conflict, match='上一项'):
        poll(env)
    assert not env.fake.calls


def test_status_reports_missing_encryption_key_without_recreating_it(env):
    env.store.key_path.unlink()
    status = env.service.read('status')
    assert not status['authorized']
    assert '恢复原密钥' in status['next_step']
    assert not env.store.key_path.exists()


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_private_database_must_not_alias_archive_sidecar(tmp_path, suffix):
    archive = tmp_path / 'archive.sqlite3'
    with pytest.raises(v.BadRequest, match='不同'):
        AlibabaOpen(archive, db_path=str(archive) + suffix, credentials=FakeCredentials())
    assert list(tmp_path.iterdir()) == []
