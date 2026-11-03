import importlib
import json

import pytest

from conftest import ROOT, load, package


def environment(root, suffix, tmp_path, monkeypatch):
    fixture = load('r7c_order_fixture_' + suffix, ROOT / 'tests/alibaba_open/test_polling.py')
    pkg = package(root, 'alibaba_open', suffix)
    for name, module in (('Client', 'protocol'), ('AlibabaOpen', 'service'), ('Store', 'store')):
        monkeypatch.setattr(fixture, name, getattr(importlib.import_module(pkg.__name__ + '.' + module), name))
    return fixture.env.__wrapped__(tmp_path), importlib.import_module(pkg.__name__ + '.errors').OpenError


def test_short_page_must_not_advance_watermark(candidate, tmp_path, monkeypatch):
    for root, suffix in ((ROOT, 'short_before'), (candidate, 'short_after')):
        env, error = environment(root, suffix, tmp_path / suffix, monkeypatch)
        env.fake.rows = [('fictional-order', env.now[0] * 1000 - 1000)]
        original = env.service.client.transport
        def short_page(method, url, params, timeout):
            status, response = original(method, url, params, timeout)
            if params.get('method', '').endswith('.list'):
                response['alibaba_seller_order_list_response']['result']['value']['total_count'] = 2
            return status, response
        env.service.client.transport = short_page
        if suffix.endswith('before'):
            env.service.write('poll', {'confirm': '1'}, 'fictional-owner')
            assert env.service._values()['watermark_ms'] == env.now[0] * 1000
        else:
            with pytest.raises(error):
                env.service.write('poll', {'confirm': '1'}, 'fictional-owner')
            assert 'watermark_ms' not in env.service._values()
            assert env.fake.calls == ['alibaba.seller.order.list']


def test_last_page_duplicate_cannot_mask_missing_order(candidate, tmp_path, monkeypatch):
    for root, suffix in ((ROOT, 'last_before'), (candidate, 'last_after')):
        env, error = environment(root, suffix, tmp_path / suffix, monkeypatch)
        now = env.now[0] * 1000 - 1000
        env.fake.rows = [('fictional-one', now), ('fictional-two', now), ('fictional-one', now)]
        if suffix.endswith('before'):
            env.service.write('poll', {'confirm': '1'}, 'fictional-owner')
            assert 'watermark_ms' in env.service._values()
        else:
            with pytest.raises(error):
                env.service.write('poll', {'confirm': '1'}, 'fictional-owner')
            assert 'watermark_ms' not in env.service._values()
            with env.archive.read() as db:
                assert db.execute('SELECT count(*) FROM deal_payments').fetchone()[0] == 0
                assert db.execute("SELECT count(*) FROM deals WHERE status='won'").fetchone()[0] == 0


def test_default_network_gate_never_reaches_transport(candidate, tmp_path, monkeypatch):
    pkg = package(candidate, 'alibaba_open', 'offline')
    protocol = importlib.import_module(pkg.__name__ + '.protocol')
    class Credentials:
        def load(self):
            return dict(app_key='fictional-app', app_secret='fictional-secret')
    monkeypatch.setattr(protocol, 'http_transport', lambda *a: pytest.fail('禁网时不能进入网络传输'))
    monkeypatch.setenv('OPS_ALIBABA_OPEN_TOP_URL', 'https://fictional.invalid/top')
    client = protocol.Client(Credentials())
    with pytest.raises(protocol.OpenError, match='尚未启用'):
        client.orders('fictional-access', 1, 2, 0, 1)
    with pytest.raises(protocol.OpenError, match='尚未启用'):
        client.exchange('fictional-code')
    with pytest.raises(protocol.OpenError, match='尚未启用'):
        client.refresh('fictional-refresh')
