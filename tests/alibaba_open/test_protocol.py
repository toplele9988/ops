import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from console.alibaba_open.errors import OpenError
from console.alibaba_open.protocol import Client, gop_sign, top_sign


class FakeCredentials:
    def load(self):
        return dict(app_key='fiction-key', app_secret='fiction-secret')


class FakeService:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, method, url, params, timeout):
        self.calls.append((url, params))
        canonical = ''.join(k + str(params[k]) for k in sorted(params) if k != 'sign')
        path = urlsplit(url).path.removeprefix('/rest')
        digest = hashlib.sha256 if path.startswith('/auth/') else hashlib.md5
        if digest == hashlib.sha256:
            canonical = path + canonical
        expected = hmac.new(b'fiction-secret', canonical.encode(), digest).hexdigest().upper()
        if params['sign'] != expected:
            return 403, {'message': 'SENTINEL_ACCESS_TOKEN'}
        assert method == 'POST' and timeout == 10
        return self.responses.pop(0)


def client(transport=None, **kwargs):
    return Client(FakeCredentials(), transport=transport, clock=lambda: 1700000000.123,
                  top_url='https://fake.invalid/top', gop_url='https://fake.invalid/rest',
                  auth_url='https://fake.invalid/authorize', redirect_uri='https://fake.invalid/return', **kwargs)


def token():
    return dict(code='0', access_token='SENTINEL_ACCESS_TOKEN', refresh_token='SENTINEL_REFRESH_TOKEN',
                expires_in='3600', refresh_expires_in='7200')


def list_response(rows=None, total=1):
    rows = rows if rows is not None else [dict(trade_id='fiction-order', modify_date={'timestamp': 1700000000000})]
    return {'alibaba_seller_order_list_response': {'result': {'success': True, 'value': {
        'order_list': {'trade_ecology_order': rows}, 'total_count': total}}}}


def order_response(status='paid'):
    return {'alibaba_seller_order_get_response': {'value': dict(trade_id='fiction-order',
        total_amount=dict(amount='123.45', currency='USD'), buyer={'immutable_eid': 'fiction-buyer'},
        trade_status=status)}}


def test_signatures_are_distinct_and_exclude_sign():
    params = dict(z='2', a='1', sign='ignored')
    assert top_sign(params, 'key') == hmac.new(b'key', b'a1z2', hashlib.md5).hexdigest().upper()
    assert gop_sign('/auth/token/create', params, 'key') == hmac.new(
        b'key', b'/auth/token/createa1z2', hashlib.sha256).hexdigest().upper()


def test_correct_and_incorrect_signatures(monkeypatch):
    service = FakeService()
    service.responses = [(200, token())]
    api = client(service)
    assert api.exchange('fiction-code')['access_token'] == 'SENTINEL_ACCESS_TOKEN'
    monkeypatch.setattr('console.alibaba_open.protocol.gop_sign', lambda *a: 'incorrect')
    with pytest.raises(OpenError) as error:
        api.exchange('fiction-code-2')
    assert error.value.reauthorize
    assert 'SENTINEL' not in str(error.value)


def test_gop_timestamp_refresh_and_request_budget():
    service = FakeService()
    service.responses = [(200, token()), (200, token())]
    count = []
    api = client(service, before_request=lambda: count.append(1))
    assert api.exchange('fiction-code')['expires_in'] == 3600
    assert api.refresh('fiction-refresh')['refresh_expires_in'] == 7200
    assert service.calls[0][1]['timestamp'] == '1700000000123'
    assert service.calls[0][1]['sign_method'] == 'sha256'
    assert len(count) == 2


def test_authorization_url_only_constructs_and_checks_state():
    api = client(lambda *a: pytest.fail('authorization must not call transport'))
    params = parse_qs(urlsplit(api.authorization_url('fiction_state_123456')).query)
    assert params['state'] == ['fiction_state_123456']
    assert params['response_type'] == ['code']
    assert params['client_id'] == ['fiction-key']
    with pytest.raises(OpenError):
        api.authorization_url('short')


def test_classic_top_and_explicit_millisecond_variant():
    params = client().top_parameters('alibaba.seller.order.list', 'fiction-access', {})
    assert params['timestamp'] == '2023-11-15 06:13:20'
    assert params['sign_method'] == 'hmac' and params['session'] == 'fiction-access'
    params = client(top_timestamp_mode='milliseconds').top_parameters('alibaba.seller.order.get', 'fiction-access', {})
    assert params['timestamp'] == '1700000000123'
    with pytest.raises(OpenError):
        client().top_parameters('alibaba.trade.order.create', 'fiction-access', {})


def test_list_pagination_and_details_use_actual_top_fields():
    service = FakeService()
    service.responses = [(200, list_response(total=2)), (200, order_response())]
    api = client(service)
    result = api.orders('fiction-access', 0, 1700000000123, 0, 1)
    assert result == dict(orders=[dict(order_id='fiction-order', modified_at_ms=1700000000000)], has_more=True)
    query = json.loads(service.calls[0][1]['param_trade_ecology_order_list_query'])
    assert query['start_page'] == 0 and query['modified_date_start'] == {'time': 0}
    order = api.order('fiction-access', 'fiction-order')
    assert order['amount'] == '123.45' and order['payment_status'] == 'paid'
    assert order['paid_at_ms'] is None and order['modified_at_ms'] is None
    assert service.calls[1][1]['e_trade_id'] == 'fiction-order'


def test_first_second_and_final_page_are_zero_based():
    service = FakeService()
    service.responses = [(200, list_response([dict(trade_id='fiction-' + str(page),
                         modify_date={'timestamp': 1700000000000})], total=3)) for page in range(3)]
    api = client(service)
    results = [api.orders('fiction-access', 0, 1700000000123, page, 1) for page in range(3)]
    assert [result['has_more'] for result in results] == [True, True, False]
    assert [json.loads(params['param_trade_ecology_order_list_query'])['start_page']
            for _, params in service.calls] == [0, 1, 2]


@pytest.mark.parametrize('status,payment', [('paid','paid'), ('undeliver','paid'),
    ('trade_close','unknown'), ('trade_success','unknown'), ('paying','unpaid'), ('unpay','unpaid')])
def test_payment_status_is_conservative(status, payment):
    service = FakeService()
    service.responses = [(200, order_response(status))]
    assert client(service).order('fiction-access', 'fiction-order')['payment_status'] == payment


@pytest.mark.parametrize('field,value', [('amount','NaN'), ('amount','-1'), ('amount','1.001'),
                                       ('amount','0e3'), ('currency','usd'), ('currency','USDT')])
def test_malformed_amount_currency_rejected(field, value):
    payload = order_response()
    payload['alibaba_seller_order_get_response']['value']['total_amount'][field] = value
    service = FakeService()
    service.responses = [(200, payload)]
    with pytest.raises(OpenError):
        client(service).order('fiction-access', 'fiction-order')


@pytest.mark.parametrize('status', [429, 500, 503])
def test_read_failure_is_retryable_but_token_failure_is_unknown(status):
    service = FakeService()
    service.responses = [(status, {'message': 'SENTINEL_ACCESS_TOKEN'}), (status, {})]
    api = client(service)
    with pytest.raises(OpenError) as error:
        api.orders('fiction-access', 0, 1, 0, 1)
    assert error.value.retryable and not error.value.unknown
    with pytest.raises(OpenError) as error:
        api.exchange('fiction-code')
    assert error.value.unknown and not error.value.retryable
    assert len(service.calls) == 2


def test_timeout_and_raw_error_never_exposes_token(caplog):
    def broken(*args):
        raise TimeoutError('SENTINEL_ACCESS_TOKEN')
    api = client(broken)
    with pytest.raises(OpenError) as error:
        api.order('SENTINEL_ACCESS_TOKEN', 'fiction-order')
    assert error.value.retryable
    assert 'SENTINEL' not in str(error.value) + caplog.text
    with pytest.raises(OpenError) as error:
        api.refresh('SENTINEL_REFRESH_TOKEN')
    assert error.value.unknown
    assert 'SENTINEL' not in str(error.value) + caplog.text


def test_default_network_is_disabled(monkeypatch):
    monkeypatch.setattr('console.alibaba_open.protocol.http_transport', lambda *a: pytest.fail('network attempted'))
    with pytest.raises(OpenError, match='尚未启用'):
        client().exchange('fiction-code')


def test_unknown_schema_and_missing_pages_fail_closed():
    service = FakeService()
    service.responses = [(200, {'orders': []}), (200, list_response([], 2))]
    api = client(service)
    for _ in range(2):
        with pytest.raises(OpenError):
            api.orders('fiction-access', 0, 1, 0, 1)
