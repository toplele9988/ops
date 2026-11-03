"""验证虚构主线、错误可见、重复运行及离线边界，不锁死缺口数量。"""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest


def test_main_line_has_no_external_effects(workflow, monkeypatch):
    from console.inbox import ai
    from console.outbox import Outbox
    from console.outbox.bridge_client import BridgeClient

    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('走查不能发送、连接渠道或调用真实模型')

    monkeypatch.setattr(Outbox, 'send', forbidden)
    monkeypatch.setattr(BridgeClient, 'deliver', forbidden)
    monkeypatch.setattr(ai, 'official_runner', forbidden)
    monkeypatch.setattr(workflow, 'make_prompt', ai.make_prompt)
    before = set(Path('/tmp').glob('fictional-workflow-*'))
    result = workflow.run_walk()
    assert result['errors'] == 0, result
    assert not calls
    assert set(Path('/tmp').glob('fictional-workflow-*')) == before
    steps = {f['step'] for f in result['findings']}
    assert {'询盘进入', '建案并关联客户', '提取产品需求', '收到供应商报价并抽取', '运费并排比价',
            '生成报价与核价', '报价人工确认并发给客户', '记录客户还价', '第二轮报价与客户接受',
            '收 30% 定金并确认成交', '下单与备货', '验货与付款后阶段推进', '发货与在途',
            '客户签收', '售后与反馈转供应商', '返单'} <= steps
    assert all(f['evidence'] and f['reason'] and f['availability'] in ('已可用', '部分可用', '缺失')
               for f in result['findings'])


def test_payment_snapshots_and_fulfilment_use_existing_services(workflow):
    with workflow.isolated() as (root, guard):
        walk = workflow.Walk(root)
        try:
            walk.inquiry()
            walk.sourcing()
            walk.freight_and_vendor()
            walk.quoting_and_payment()
            first, second = walk.quote(walk.rid), walk.quote(walk.second)
            assert first['status'] == 'superseded' and second['status'] == 'accepted'
            assert second['totals']['revenue_minor'] * 30 // 100 == 180000
            assert first['lines'][0]['sell_unit_price'] == '260.00'
            assert second['lines'][0]['sell_unit_price'] == '250.00'
            assert first['totals']['cost_cny'] == second['totals']['cost_cny'] == '34548.00'
            payment = walk.fulfil.read('payments', row_id=walk.did)['items'][0]
            assert payment['status'] == 'confirmed' and payment['quote_round_id'] == walk.second
            walk.delivery()
            assert walk.fulfil.read('fulfilment', row_id=walk.did)['done'] == 8
            assert walk.fulfil.read('issue_summary')['unresolved'] == 0
            assert not walk.fulfil.read('reorder_leads')['items']
            assert not guard.violations
        finally:
            walk.db.close()


def test_repeated_walks_are_independent_and_deterministic(workflow):
    first, second = workflow.run_walk(), workflow.run_walk()
    assert first['errors'] == second['errors'] == 0
    assert first == second


def test_business_failures_are_visible_and_never_count_as_pass(workflow, monkeypatch, capsys):
    def fail(*args):
        raise workflow.WalkFailure('虚构前置步骤校验未通过，不能继续主线')
    monkeypatch.setattr(workflow.Walk, 'inquiry', fail)
    assert workflow.main([]) == 1
    text = capsys.readouterr().out
    assert '执行错误' in text and '虚构前置步骤校验未通过' in text
    assert 'Traceback' not in text


def test_unexpected_exception_does_not_print_sensitive_diagnostics(workflow, monkeypatch):
    def fail(*args):
        raise RuntimeError('FICTIONAL-PRIVATE-DIAGNOSTIC')
    monkeypatch.setattr(workflow.Walk, 'inquiry', fail)
    result = workflow.run_walk()
    assert result['errors'] == 1 and result['error_type'] == 'RuntimeError'
    assert 'FICTIONAL-PRIVATE-DIAGNOSTIC' not in json.dumps(result)


@pytest.mark.parametrize('action', ('socket', 'subprocess', 'sqlite', 'write', 'read'))
def test_guard_stops_external_access_before_use(workflow, tmp_path, action):
    outside = tmp_path / 'fictional-external.txt'
    outside.write_text('虚构外部文件')
    before = outside.read_bytes()
    with workflow.isolated() as (root, guard):
        with pytest.raises(workflow.WalkFailure, match='离线隔离'):
            if action == 'socket':
                socket.socket()
            elif action == 'subprocess':
                subprocess.Popen([sys.executable, '-c', 'pass'])
            elif action == 'sqlite':
                sqlite3.connect(outside)
            elif action == 'write':
                outside.write_text('不得写入')
            else:
                outside.read_bytes()
        assert guard.violations == 1
    assert outside.read_bytes() == before


def test_swallowed_guard_failure_still_fails_walk(workflow, monkeypatch):
    original = workflow.Walk.inquiry
    def attempt(walk):
        try:
            socket.socket()
        except workflow.WalkFailure:
            pass
        return original(walk)
    monkeypatch.setattr(workflow.Walk, 'inquiry', attempt)
    result = workflow.run_walk()
    assert result['errors'] == 1
    assert '曾尝试访问外部资源' in result['error']


def test_no_external_path_cli_and_strict_exit_code(workflow):
    command = [sys.executable, str(workflow.ROOT / 'scripts/workflow_walk.py')]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([*command, '--strict', '--json'], env=env, text=True, capture_output=True)
    payload = json.loads(result.stdout)
    assert payload['errors'] == 0
    assert result.returncode == (2 if payload['blocked'] else 0)
    assert not result.stderr
    bad = subprocess.run([*command, '--archive', '/tmp/fictional-existing.sqlite3'], env=env, text=True, capture_output=True)
    assert bad.returncode == 2


def test_missing_media_fake_can_never_sign_or_read(workflow):
    if not workflow.MISSING_MEDIA:
        pytest.skip('完整源码已包含媒体包，无需导入替身')
    with pytest.raises(RuntimeError, match='禁用媒体签名'):
        workflow.tokens.make(['fictional'])
    with pytest.raises(RuntimeError, match='禁用媒体签名'):
        workflow.tokens.read('fictional-invalid')
