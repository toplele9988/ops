import json

from console.channel_plan import DEFAULT_PLAN, public_plan, resolve_plan
from console.audit.service import Audit


def test_plan_defaults_and_role_counts_are_fixed(tmp_path):
    assert resolve_plan(path=tmp_path / 'missing.json') == DEFAULT_PLAN
    exposed = public_plan(DEFAULT_PLAN)
    assert exposed['alibaba_accounts'] == {
        'main': 'operating', 'others': 'planned', 'expected': 6,
        'main_count': 1, 'others_count': 5,
    }


def test_injected_plan_file_reads_only_roles_and_counts(tmp_path):
    path = tmp_path / 'operation-plan.json'
    value = {
        'whatsapp': 'operating',
        'alibaba_accounts': {'main': 'operating', 'others': 'operating'},
        'wechat': 'operating',
    }
    path.write_text(json.dumps(value))
    assert resolve_plan(path=path) == value


def test_plan_read_failure_and_invalid_shape_fall_back_without_leaking(tmp_path):
    path = tmp_path / 'operation-plan.json'
    assert resolve_plan(path=path) == DEFAULT_PLAN
    path.write_text('{not-json; token=FAKE-PLAN-SECRET}')
    assert resolve_plan(path=path) == DEFAULT_PLAN
    path.write_text(json.dumps({
        'whatsapp': 'operating',
        'alibaba_accounts': {'main': 'operating', 'others': 'planned'},
        'wechat': 'operating',
        'token': 'FAKE-PLAN-SECRET',
    }))
    result = resolve_plan(path=path)
    assert result == DEFAULT_PLAN
    assert 'FAKE-PLAN-SECRET' not in json.dumps(result)


def test_plan_symlink_is_rejected_and_defaults_used(tmp_path):
    target = tmp_path / 'target.json'
    target.write_text(json.dumps({
        'whatsapp': 'operating',
        'alibaba_accounts': {'main': 'operating', 'others': 'operating'},
        'wechat': 'operating',
    }))
    alias = tmp_path / 'alias.json'
    alias.symlink_to(target)
    assert resolve_plan(path=alias) == DEFAULT_PLAN


def test_audit_exposes_injected_plan_without_replacing_existing_fields(tmp_path):
    plan_path = tmp_path / 'operation-plan.json'
    plan_path.write_text(json.dumps({
        'whatsapp': 'operating',
        'alibaba_accounts': {'main': 'operating', 'others': 'planned'},
        'wechat': 'operating',
    }))
    result = Audit(tmp_path / 'archive.sqlite3', plan_path=plan_path, checks={}).scan()
    assert result['plan']['whatsapp'] == 'operating'
    assert result['summary'] == '0 个需要处理'
    assert result['attention_count'] == result['planned_count'] == 0
    assert result['total'] == 0


def test_direct_plan_injection_is_allowlisted_and_safe():
    injected = {
        'whatsapp': 'planned',
        'alibaba_accounts': {'main': 'operating', 'others': 'not_started'},
        'wechat': 'operating',
    }
    assert resolve_plan(injected, path='/unused') == injected
