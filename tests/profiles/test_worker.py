import json
import sqlite3
import subprocess

import pytest

from console.profiles.store import Profiles
from console.profiles.worker import Worker
from console.profiles.windows import line, normalise, windows
from conftest import Inbox, NOW, Runner, conversation, message


def test_windows_chronological_labels_media_and_long_message():
    messages = [message(3), message(1, '甲' * 15000), dict(message(2), blocks=[dict(type='image'), dict(type='file', name='虚构.xlsx')])]
    ordered = normalise(messages)
    chunks = windows(ordered)
    assert all(sum(len(line(m)) + 1 for m in w) <= 6000 for w in chunks)
    flat = [m for w in chunks for m in w]
    assert [m['message_index'] for m in flat] == sorted(m['message_index'] for m in flat)
    assert ''.join(m['text'] for m in flat if m['message_index'] == 1) == '甲' * 15000
    assert flat[-2]['text'] == '[图片]\n[文件 虚构.xlsx]'
    assert '虚构成员' in line(flat[0]) and 'UTC' in line(flat[0])


def test_partial_fills_from_both_ends_and_replays_nothing(tmp_path):
    conv = conversation([message(i, '虚构询价' + '甲' * 3900) for i in range(1, 8)])
    runner = Runner()
    worker = Worker(tmp_path / 'profiles.sqlite3', inbox=Inbox(conv), runner=runner, clock=lambda: NOW)
    first = worker.run(max_windows=2)
    assert first['windows'] == 2
    p = first['profiles'][0]
    assert (p['messages_read'], p['messages_total'], p['state']) == (2, 7, 'partial')
    maps = [p for p in runner.prompts if '\n消息：\n' in p]
    assert maps[0].split('\n消息：\n')[1].startswith('#7 ')
    assert maps[1].split('\n消息：\n')[1].startswith('#1 ')
    worker.run(max_windows=2)
    final = worker.run(max_windows=30)['profiles'][0]
    assert (final['messages_read'], final['messages_total'], final['state']) == (7, 7, 'ready')
    count = len(runner.prompts)
    assert worker.run()['conversations'] == 0
    assert len(runner.prompts) == count
    assert len([p for p in runner.prompts if '\n消息：\n' in p]) == 7


def test_incremental_threshold_and_only_new_windows(tmp_path):
    conv = conversation()
    now = [NOW]
    runner = Runner()
    worker = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=runner, clock=lambda: now[0])
    worker.run()
    conv['messages'].extend(message(i) for i in range(2, 31))
    conv['last_seen'] = conv['messages'][-1]['sent_at']
    assert worker.run()['windows'] == 0
    assert worker.run()['calls'] == 0
    conv['messages'].append(message(31))
    added = worker.run()
    assert added['windows'] == 1
    assert added['profiles'][0]['messages_read'] == 31
    assert runner.prompts[-2].split('\n消息：\n')[1].startswith('#2 ')
    conv['messages'].append(message(32))
    now[0] += 8 * 86400
    assert worker.run()['windows'] == 1
    assert runner.prompts[-2].split('\n消息：\n')[1].startswith('#32 ')


@pytest.mark.parametrize('title,kind', [('虚构一家人', 'personal'), ('虚构系统通知', 'system')])
def test_rules_do_not_call_model(tmp_path, title, kind):
    runner = Runner()
    conv = conversation([message(1, '晚饭回家吃')], title=title)
    result = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=runner, clock=lambda: NOW).run()
    assert result['rules'] == 1 and runner.prompts == []
    assert result['profiles'][0]['kind'] == kind
    assert result['profiles'][0]['state'] == 'skipped'


def test_personal_title_with_business_is_not_skipped(tmp_path):
    runner = Runner()
    conv = conversation(title='虚构家庭群')
    assert Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=runner, clock=lambda: NOW).run()['rules'] == 0
    assert runner.prompts


@pytest.mark.parametrize('failure', ['{}', '{bad', subprocess.TimeoutExpired('fictional', 1)])
def test_bad_model_preserves_existing_profile(tmp_path, failure):
    conv, runner = conversation(), Runner()
    path = tmp_path / 'p.sqlite3'
    worker = Worker(path, inbox=Inbox(conv), runner=runner, clock=lambda: NOW)
    worker.run()
    before = Profiles(path).get(conv['key'])
    conv['messages'].extend(message(i) for i in range(2, 32))
    runner.fail = failure
    assert worker.run()['failed_conversations'] == 1
    assert Profiles(path).get(conv['key']) == before
    runner.fail = None
    assert worker.run()['profiles'][0]['messages_read'] == 31


@pytest.mark.parametrize('text,role,stage', [('虚构报价 ZX 已发', 'seller', 'quoted'), ('虚构已付款', 'buyer', 'customer')])
def test_stage_from_verified_evidence(tmp_path, text, role, stage):
    conv = conversation([message(1, text, role)])
    result = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=Runner(), clock=lambda: NOW).run()
    assert result['profiles'][0]['customer_stage'] == stage


def test_dry_run_does_not_create_or_change_side_database(tmp_path):
    path = tmp_path / 'not-created' / 'p.sqlite3'
    worker = Worker(path, inbox=Inbox(conversation()), runner=Runner(), clock=lambda: NOW)
    report = tmp_path / 'report'
    result = worker.run(dry_run=True, report=report)
    assert result['calls'] == 1 and not path.parent.exists()
    assert set(json.loads((report / 'profiles.json').read_text())) == {'summary', 'profiles'}
    worker.run()
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    worker.run(dry_run=True, report=report)
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before


def test_daily_budget_and_resume(tmp_path):
    path = tmp_path / 'p.sqlite3'
    conv = conversation()
    runner = Runner()
    worker = Worker(path, inbox=Inbox(conv), runner=runner, clock=lambda: NOW)
    assert worker.run(daily_budget_tokens=1)['budget_stopped']
    assert runner.prompts == []
    assert worker.run()['profiles'][0]['state'] == 'ready'
    with sqlite3.connect(path) as db:
        db.execute('UPDATE cp_call SET charged_tokens=1000000')
    conv['messages'].extend(message(i) for i in range(2, 32))
    assert worker.run()['budget_stopped']
    assert len(runner.prompts) == 1


def test_history_change_rebuilds_and_incomplete_read_never_ready(tmp_path):
    conv = conversation()
    worker = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=Runner(), clock=lambda: NOW)
    worker.run()
    conv['messages'].insert(0, message(-1))
    assert worker.run()['profiles'][0]['messages_read'] == 2
    before = Profiles(worker.path).get(conv['key'])
    conv['history_complete'] = False
    assert worker.run()['failed_conversations'] == 1
    assert Profiles(worker.path).get(conv['key']) == before


def test_multi_level_reduce_and_resume_after_failed_reduce(tmp_path):
    from copy import deepcopy
    from console.profiles.model import map_prompt
    from console.profiles.store import working_database
    worker = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conversation()), runner=Runner(), clock=lambda: NOW)
    value, sources = None, normalise([message(1)])
    raw, _ = worker.task.extract(map_prompt(conversation(), sources))
    from console.profiles.model import clean_answer
    value = clean_answer(raw, sources)
    parts = []
    for i in range(50):
        item = deepcopy(value)
        item['products'] = ['虚构产品' * 30 + str(i)] * 10
        item['summary'] = '虚构汇总' + str(i)
        parts.append(item)
    with working_database(worker.path) as db:
        with db:
            run_id = db.execute('INSERT INTO cp_run(started_at) VALUES(?)', (NOW,)).lastrowid
        answer, model = worker._reduce(db, run_id, parts, sources, NOW, 1000000)
        count = len(worker.task.runner.prompts)
        assert answer['kind'] == 'customer' and count > 2
        assert worker._reduce(db, run_id, parts, sources, NOW, 1000000)[0] == answer
        assert len(worker.task.runner.prompts) == count


def test_reduce_invalid_answer_preserves_existing_and_retries(tmp_path):
    conv, runner = conversation(), Runner()
    worker = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=runner, clock=lambda: NOW)
    worker.run()
    before = Profiles(worker.path).get(conv['key'])
    conv['messages'].extend(message(i) for i in range(2, 32))
    original = runner.__class__.__call__
    class BadReduce(Runner):
        def __call__(self, argv, **kw):
            if '合并以下按时间窗口提取的结果' in kw['input']:
                return type('Answer', (), dict(stdout='{}', returncode=0))()
            return original(self, argv, **kw)
    worker.task.runner = BadReduce()
    assert worker.run()['failed_conversations'] == 1
    assert Profiles(worker.path).get(conv['key']) == before
    worker.task.runner = Runner()
    result = worker.run()
    assert result['windows'] == 0 and result['profiles'][0]['messages_read'] == 31


def test_single_long_message_counts_only_after_all_fragments_read(tmp_path):
    conv = conversation([message(1, '虚构询价' + '甲' * 16000)])
    worker = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=Runner(), clock=lambda: NOW)
    assert worker.run(max_windows=2)['profiles'][0]['messages_read'] == 0
    assert worker.run(max_windows=2)['profiles'][0]['messages_read'] == 1


def test_old_sale_evidence_survives_reduce_selection():
    from console.profiles.worker import preserve_events
    paid = dict(claim='paid', quote='虚构已付款', message_index=1, sent_at=NOW - 1000)
    issue = dict(claim='after_sales', quote='虚构售后维修', message_index=2, sent_at=NOW)
    answer = dict(evidence=[issue])
    result = preserve_events(answer, [dict(evidence=[paid]), dict(evidence=[issue])])
    assert paid in result['evidence']


def test_fractional_timestamps_and_report_excludes_raw_windows(tmp_path):
    conv = conversation([dict(message(1), sent_at=NOW + .125)])
    runner = Runner()
    report = tmp_path / 'report'
    result = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=runner, clock=lambda: NOW + 1000).run(report=report)
    assert result['profiles'][0]['state'] == 'ready'
    payload = json.loads((report / 'profiles.json').read_text())
    assert payload['profiles'][0]['evidence_json'][0]['sent_at'] == NOW + .125
    assert all(type(v) in (int, float, bool) for v in payload['summary'].values())
    assert 'source_json' not in (report / 'profiles.json').read_text()


def test_report_inside_repository_is_rejected_before_model(tmp_path):
    from pathlib import Path
    runner = Runner()
    worker = Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conversation()), runner=runner, clock=lambda: NOW)
    with pytest.raises(ValueError):
        worker.run(dry_run=True, report=Path(__file__).resolve().parents[2] / 'private_out')
    assert runner.prompts == [] and not worker.path.exists()


def test_known_supplier_hint_reaches_prompt(tmp_path):
    conv, runner = conversation(), Runner()
    Worker(tmp_path / 'p.sqlite3', inbox=Inbox(conv), runner=runner, clock=lambda: NOW,
           hints={conv['key']: '供应商档案记录：该群是供应商「虚构灯光厂」的工作群。'}).run()
    assert all('已知资料：供应商档案记录' in p for p in runner.prompts)


@pytest.mark.parametrize('change', [dict(kind='peer', confidence=0.9), dict(kind='unknown', confidence=0.3, evidence=[])])
def test_non_business_group_stops_early_and_then_only_reads_new(tmp_path, change):
    conv = conversation([message(i) for i in range(1, 400)])
    runner = Runner()
    runner.answer = lambda prompt, value: dict(value, **change)
    path = tmp_path / 'p.sqlite3'
    worker = Worker(path, inbox=Inbox(conv), runner=runner, clock=lambda: NOW)
    worker.run(max_windows=30)
    first = len(runner.prompts)
    assert first <= 6
    profile = Profiles(path).get(conv['key'])
    assert profile['kind'] == change['kind'] and profile['state'] == 'partial'
    # Later runs: nothing new, so the long unread history is not read further.
    worker.run(max_windows=30)
    assert len(runner.prompts) == first
