from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from .conftest import answer


def paid_deal(env, *, price='20.00', currency='USD'):
    did, rid, *_ = env.good_round(amount=price, currency=currency)
    env.qw('status', rid, action='send', confirm='1')
    env.qw('status', rid, action='accept')
    env.historical_paid(did)
    return did, rid


def test_paid_definition_period_counts_amounts_by_currency_and_missing(env):
    today = env.clock()
    env.clock.now = datetime(2030, 1, 3, 8, tzinfo=timezone.utc).timestamp()
    paid_deal(env, price='10.00')
    env.clock.now = datetime(2030, 1, 8, 8, tzinfo=timezone.utc).timestamp()
    paid_deal(env, price='20.00')
    env.clock.now = today
    did, rid = paid_deal(env, price='30.00', currency='EUR')
    missing = env.deal(stage='paid', title='虚构缺金额成交')
    negotiating, quote, *_ = env.good_round()
    env.qw('status', quote, action='send', confirm='1')
    env.qw('status', quote, action='accept')  # Acceptance is not payment.
    lost = env.deal(title='虚构丢单')
    env.dw('update', lost, status='lost', lost_reason='虚构预算原因')
    result = env.roles.run_brief('fictional-owner')
    out = result['output_json']
    assert out['results']['today']['count'] == 2
    assert out['results']['week']['count'] == 3
    assert out['results']['month']['count'] == 4
    assert out['results']['today']['amounts'] == {'EUR': '60.00'}
    assert out['results']['week']['amounts'] == {'EUR': '60.00', 'USD': '40.00'}
    assert out['results']['month']['amounts'] == {'EUR': '60.00', 'USD': '60.00'}
    assert out['results']['today']['amount_pending'] == 1 and '金额待补' in result['output_text']
    assert out['negotiating'] == 1 and out['lost_this_week']['count'] == 1
    assert out['quotes_this_week'] == dict(sent=3, accepted=3)
    assert next(s for s in out['by_stage'] if s['stage'] == 'awaiting_payment')['open_count'] == 1


def test_acceptance_counts_event_date_not_later_notes(env):
    today = env.clock()
    env.clock.now = datetime(2030, 1, 3, 8, tzinfo=timezone.utc).timestamp()
    did, rid = paid_deal(env)
    env.clock.now = today
    env.qw('update', rid, notes='虚构本周更改备注')
    env.dw('update', did, stage='inspection')
    out = env.roles.run_brief('fictional-owner')['output_json']
    assert out['quotes_this_week']['accepted'] == 0
    assert out['results']['week']['count'] == 0
    assert out['results']['month']['count'] == 1


def test_latest_accepted_round_and_revenue_not_deal_value(env):
    did, rid = paid_deal(env, price='10.00')
    new = env.qw('create', did)
    detail = env.quotes.read('detail', row_id=new)
    env.qw('update', new, valid_until='2030-02-20')
    env.qw('line_update', new, detail['lines'][0]['id'], sell_unit_price='30.00')
    env.qw('terms', new, confirm='1', payment='30_70', delivery_scope='虚构交付范围：按本轮约定交付')
    env.qw('status', new, action='send', confirm='1')
    env.qw('status', new, action='accept')
    env.dw('update', did, value='9999.99')
    out = env.roles.run_brief('fictional-owner')['output_json']
    assert out['results']['today']['count'] == 1
    assert out['results']['today']['amounts'] == {'USD': '60.00'}
    assert out['quotes_this_week'] == dict(sent=2, accepted=2)


def test_brief_ai_only_polishes_narrative_and_reuses_same_day(env):
    paid_deal(env)
    runner = Mock(return_value=answer('今日新成交 1 单，请核对待办。'))
    service = env.service(runner=runner)
    first = service.run_brief('fictional-owner')
    assert first['source'] == 'ai' and first['output_json']['results']['today']['amounts'] == {'USD': '40.00'}
    assert service.run_brief('fictional-owner')['id'] == first['id']
    assert service.read('brief')['id'] == first['id'] and runner.call_count == 1
    env.deal()
    assert not service.run_brief('fictional-owner')['reused']
    assert runner.call_count == 2


def test_bad_brief_numbers_and_ai_failure_return_template(env):
    paid_deal(env)
    for output in ('', answer('今日成交 999999 单')):
        env.clock.advance(86400)
        result = env.service(runner=Mock(return_value=output)).run_brief('fictional-owner')
        assert result['source'] == 'rules' and result['output_json']['narrative']
        assert '999999' not in result['output_text']


def test_priorities_include_alerts_secretary_headlines_and_triage_count(env):
    key = env.conv()
    did = env.deal(key=key, stage='awaiting_payment')
    env.clock.advance(3 * 86400)
    env.roles.run_patrol('fictional-owner')
    env.roles.run_secretary(did, 'fictional-owner')
    env.conv(name='虚构新客户')
    env.roles.scan_triage('fictional-owner')
    output = env.roles.run_brief('fictional-owner')['output_json']
    assert len(output['priorities']) <= 5
    assert output['priorities'][0]['kind'] == 'alert' and output['priorities'][0]['headline']
    assert any(p['kind'] == 'triage' for p in output['priorities'])
    assert output['pending_triage'] == 1


def test_business_timezone_changes_today_without_host_clock(env):
    from dataclasses import replace
    env.clock.now = datetime(2030, 1, 9, 23, 30, tzinfo=timezone.utc).timestamp()
    paid_deal(env)
    service = env.service(config=replace(env.roles.config, timezone='Asia/Shanghai'))
    result = service.run_brief('fictional-owner')
    assert result['subject_key'] == '2030-01-10'
    assert result['output_json']['results']['today']['count'] == 1


def test_future_and_payment_rollback_are_not_counted(env):
    did, _ = paid_deal(env)
    env.dw('update', did, stage='quoting')
    now = env.clock()
    env.clock.advance(86400)
    paid_deal(env)
    env.clock.now = now
    result = env.roles.run_brief('fictional-owner')['output_json']
    assert result['results']['today']['count'] == 0


def test_brief_date_formatted_money_and_count_survive_ai_redaction(env):
    for _ in range(3):
        paid_deal(env, price='205.75')
    text = '成交简报（2030-01-10）：3 单，USD 1,234.50；+86 138 0013 8000；fictional@example.invalid'
    runner = Mock(return_value=answer(text))
    roles = env.service(runner=runner)
    result = roles.run_brief('fictional-owner')
    assert result['source'] == 'ai' and result['ai_success']
    assert result['output_json']['results']['today']['amounts'] == {'USD': '1234.50'}
    for fact in ('2030-01-10', '1234.50', '3 单'):
        assert fact in runner.call_args.args[0]
    for rendered in (result['output_text'], result['output_json']['narrative'], roles.read('brief')['output_text']):
        for fact in ('2030-01-10', 'USD 1,234.50', '3 单'):
            assert fact in rendered
        assert '〔号码已隐藏〕' in rendered and '〔邮箱已隐藏〕' in rendered
        assert '138 0013' not in rendered and 'fictional@example.invalid' not in rendered


@pytest.mark.parametrize('amount', ['USD 999999999999', 'USD 9,999.00'])
def test_business_number_redaction_cannot_hide_invented_ai_amounts(env, amount):
    paid_deal(env)
    result = env.service(runner=Mock(return_value=answer('今日成交金额 ' + amount))).run_brief('fictional-owner')
    assert result['source'] == 'rules' and result['status'] == 'fallback'
    assert '9999' not in result['output_text']


def test_brief_rate_limit_keeps_rules_and_fixed_reason(env, monkeypatch):
    from console.roles.storage import RateLimited
    from console.roles import service

    paid_deal(env)
    def limited(*args):
        raise RateLimited(60, '岗位 AI 调用太频繁，请稍后再试')
    monkeypatch.setattr(service, 'reserve', limited)
    result = env.service(runner=Mock()).run_brief('fictional-owner')
    assert result['source'] == 'rules' and result['status'] == 'fallback'
    assert result['error'] == '岗位 AI 调用太频繁，请稍后再试'
    assert result['output_json']['results']['today']['amounts'] == {'USD': '40.00'}
