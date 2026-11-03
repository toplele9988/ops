"""The manual CLI uses fictional local inputs and an injected runner only."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from console.archive.store import Store
from console.extract.service import Extract
from console.inbox.model import make_key
from console.roles.ai import DEFAULT_RUNNER

ROOT = Path(__file__).resolve().parents[2]


def load_cli():
    spec = importlib.util.spec_from_file_location('extract_cli_for_test', ROOT / 'scripts' / 'extract_run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cli_env(tmp_path):
    now = 1894262400
    key = make_key('conversation', 'wechat', 'fictional-account', 'fictional-supplier')
    conv = dict(key=key, channel='wechat', channel_label='微信', account='fictional-account',
                buyer_name='SUP-123 虚构灯具', counterpart_role='supplier', is_group=True,
                first_seen=now, last_seen=now, n_messages=1,
                messages=[dict(role='buyer', text='虚构灯具 1280元/台，含税，本周有效', sent_at=now)])
    inbox = SimpleNamespace(read=lambda at: dict(ready=True, conversations={key: conv}),
                            detail=lambda lookup, at: conv if lookup == key else None)
    path = tmp_path / 'fictional-archive.sqlite3'
    runner = Mock(side_effect=AssertionError('preview must never call AI'))
    service = Extract(path, inbox=inbox, runner=runner, clock=lambda: now)
    return SimpleNamespace(path=path, key=key, inbox=inbox, service=service, runner=runner, script=load_cli())


def test_default_preview_does_not_create_db_or_call_runner(cli_env):
    result = cli_env.script.run(cli_env.service)
    assert result['dry_run'] and result['scanned'] == 1
    assert not cli_env.path.exists()
    cli_env.runner.assert_not_called()


def test_preview_keeps_existing_database_bytes_and_apply_writes_pending(cli_env):
    Store(cli_env.path).initialize()
    before = cli_env.path.read_bytes()
    assert cli_env.script.run(cli_env.service)['dry_run']
    assert cli_env.path.read_bytes() == before
    cli_env.service.runner = None
    result = cli_env.script.run(cli_env.service, apply=True, who='fictional-cli-owner')
    assert not result['dry_run'] and result['added'] == 1
    suggestions = cli_env.service.read('suggestions')['items']
    assert len(suggestions) == 1 and suggestions[0]['status'] == 'pending'
    with cli_env.service.store.read() as db:
        assert db.execute('SELECT count(*) FROM offers').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM freight_quotes').fetchone()[0] == 0
        assert db.execute("SELECT created_by FROM role_runs WHERE role='extract' ORDER BY id LIMIT 1").fetchone()[0] == 'fictional-cli-owner'


def test_run_passes_scan_options_under_shared_lock(cli_env):
    with patch.object(cli_env.service, 'scan', return_value={'dry_run': True}) as scan:
        cli_env.script.run(cli_env.service, conversation_key=cli_env.key, days=10, limit=2)
    scan.assert_called_once_with('extract-cli', apply=False, conversation_key=cli_env.key, days=10, limit=2)


def test_main_defaults_to_rules_and_disables_network_sources(cli_env, capsys):
    service = Mock(store=cli_env.service.store)
    service.scan.return_value = dict(dry_run=True, scanned=0, added=0, remaining=0)
    with patch.object(cli_env.script, 'shared_inbox', return_value=cli_env.inbox) as inbox, \
         patch.object(cli_env.script, 'Extract', return_value=service) as factory:
        assert cli_env.script.main(['--archive', str(cli_env.path)]) == 0
    assert json.loads(capsys.readouterr().out)['dry_run']
    options = inbox.call_args.kwargs
    assert options['wechat_path'] == cli_env.path.parent / 'disabled-wechat.sqlite3'
    assert options['whatsapp_config_path'] == cli_env.path.parent / 'disabled-whatsapp.json'
    with pytest.raises(RuntimeError, match='未启用网络'):
        options['fetch']('https://fictional.invalid')
    assert factory.call_args.kwargs['runner'] is None
    assert service.scan.call_args.kwargs['apply'] is False


def test_ai_requires_explicit_apply_and_main_never_calls_official_runner(cli_env, capsys):
    with pytest.raises(SystemExit) as result:
        cli_env.script.main(['--archive', str(cli_env.path), '--ai'])
    assert result.value.code == 2 and '--apply' in capsys.readouterr().err
    service = Mock(store=cli_env.service.store)
    service.scan.return_value = dict(dry_run=False, scanned=0, added=0, remaining=0)
    with patch.object(cli_env.script, 'shared_inbox', return_value=cli_env.inbox), \
         patch.object(cli_env.script, 'Extract', return_value=service) as factory:
        assert cli_env.script.main(['--archive', str(cli_env.path), '--apply', '--ai']) == 0
    assert factory.call_args.kwargs['runner'] is DEFAULT_RUNNER
    assert service.scan.call_args.kwargs['apply'] is True


@pytest.mark.parametrize('args', [['--days', '0'], ['--days', '366'], ['--limit', '0'], ['--limit', '101']])
def test_cli_bounds_reject_before_creating_service(cli_env, args):
    with patch.object(cli_env.script, 'Extract', side_effect=AssertionError('invalid CLI constructed service')):
        with pytest.raises(SystemExit) as result:
            cli_env.script.main(['--archive', str(cli_env.path), *args])
    assert result.value.code == 2 and not cli_env.path.exists()


def test_cli_errors_hide_private_paths_and_values(cli_env, capsys):
    with patch.object(cli_env.script, 'shared_inbox', side_effect=RuntimeError('/fictional/private/API-SECRET')):
        assert cli_env.script.main(['--archive', str(cli_env.path)]) == 1
    message = capsys.readouterr().err
    assert '报价抽取员' in message and '/fictional' not in message and 'API-SECRET' not in message
