"""原授权替身逐项复核；所有文件、配置与数据库都限于临时目录。"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
import stat
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[2] / 'deploy/kb'
if str(HERE) not in sys.path:
    sys.path.append(str(HERE))
import prepare_install as p
import authorization_review as r
from authorization_review_fixtures import (provenance_fixture, session_fixture,
                                           stage_marker, tmp_only)

ORIGINAL_CANDIDATES = r.Backend.candidates


def make_unit(identifier, room, rows, *, document=False):
    raw = identifier + '\0' + ''.join(row['evidence_id'] + '\0' + row['body'] + '\0' for row in rows)
    return {'unit_id': identifier, 'chatroom_id': room, 'unit_sha256': p.b.sha(raw),
            'text': '\n'.join('[' + str(index) + '] ' + row['body']
                              for index, row in enumerate(rows, 1)), 'rows': deepcopy(rows)}


def fake_extract():
    return SimpleNamespace(PROMPT='虚构供应资料提示词\n', LEARNING_PROMPT='虚构学习资料提示词\n',
        _eligible=lambda row: row['price_kind'] != 'customer_inquiry' and bool(row['body_text'].strip()),
        _make_unit=make_unit, DOC_CHUNKS=12,
        _document_units=lambda row: [make_unit('KD1:' + row['evidence_id'], row['chatroom_id'],
            [{'evidence_id': row['evidence_id'], 'body': row['body_text'],
              'at': row['source_created_at']}], document=True)],
        cpq_windows=SimpleNamespace(_one_line=lambda text: re.sub(r'\s+', ' ', text).strip(),
                                    _time=lambda row: row['source_created_at']))


def stable_unit(extract, row):
    unit = extract._make_unit('KL:' + row['evidence_id'], row['chatroom_id'], [
        {'evidence_id': row['evidence_id'], 'body': row['body_text'], 'at': row['source_created_at']}])
    unit['business_role'] = row['role']
    unit['live_increment'] = True
    unit['unit_sha256'] = p.b.sha(row['role'] + '\0' + unit['unit_sha256'])
    return unit


def fake_core():
    def windows(group):
        record = group['records'][0]
        return [{'segments': [{'inbound_id': record['inbound_id'], 'text': record['body']}],
                 'evidence_spans': [{'inbound_id': record['inbound_id'], 'text': record['body']}]}]
    return SimpleNamespace(normalize_record=lambda record: deepcopy(record),
        record_version=lambda records: p.b.digest(records), plan_windows=windows,
        validate_coverage=lambda records, spans: {'complete': True},
        prompt_for=lambda group, window: '虚构阿里提示词\n' + group['records'][0]['body'])


@pytest.fixture
def fictional(tmp_path, monkeypatch):
    case = session_fixture(tmp_path)
    extract = fake_extract()
    rows = [dict(evidence_id='fictional-source-' + str(number), chatroom_id='fictional-room-' + str(number),
                 classification_id='fictional-class', owner='fictional-owner',
                 source_scope='fictional-scope', source_created_at=200 + number,
                 received_at=201 + number, created_at=202 + number,
                 source_kind='message', source_sha256=p.b.sha('fictional-original-' + str(number)),
                 body_text='完全虚构的资料参数' + str(number), price_kind='not_price',
                 role='supplier', channel_role='CHANNEL_ROLE_SUPPLIER', is_cost_price=0,
                 message_id='fictional-message-' + str(number), asset_id=None)
            for number in range(2)]
    record = {'inbound_id': 'fictional-alibaba-source', 'source_sha256': p.b.sha('fictional-ali-original'),
              'account': 'fictional-account', 'conversation_id': '123456', 'role': 'seller',
              'body': '完全虚构的阿里资料参数', 'sent_at': '1970-01-01T00:04:00+00:00',
              'received_at': '1970-01-01T00:04:01+00:00', 'scope': 'fictional-ali-scope',
              'message_id': 'fictional-ali-message', 'receiver_fingerprint': p.b.sha('fictional-receiver')}
    group = {'key': p.b.sha('fictional-group'), 'owner': 'fictional-owner',
             'account': record['account'], 'conversation_id': record['conversation_id'],
             'records': [record], 'source_version': p.b.digest([record])}
    case.extract = extract
    case.wechat = [{'row': row, 'units': [stable_unit(extract, row)], 'blocked': None} for row in rows]
    case.alibaba = [{'group': group, 'record': record, 'blocked': None}]
    case.current_rows = deepcopy(rows)
    case.current_groups = {group['key']: deepcopy(group)}
    case.learning = {}
    case.calls = []
    case.failure = None
    case.noise = False
    case.side_effect = None

    def provenance(ws, als):
        case.calls.append('provenance')
        assert ws == case.root / 'snapshot/wechat' and als == case.root / 'snapshot/alibaba'
        return {}, deepcopy(case.provenance)

    def candidates(ws, als, quarantine):
        case.calls.append('candidates')
        assert quarantine == case.quarantine
        return deepcopy(case.wechat), deepcopy(case.alibaba)

    def wechat_rows(db, state):
        case.calls.append('wechat_rows')
        assert db.execute('SELECT value FROM fictional_fixture').fetchone()[0] == '完全虚构，仅供隔离测试'
        assert state.execute('SELECT value FROM fictional_fixture').fetchone()[0] == '完全虚构，仅供隔离测试'
        return extract, {'_learning': deepcopy(case.learning), '_supplier_context': {
            'channels': {row['chatroom_id']: '虚构' for row in case.current_rows}}}, deepcopy(case.current_rows)

    def alibaba_groups(inbound, ledger):
        case.calls.append('alibaba_groups')
        assert inbound.execute('SELECT value FROM fictional_fixture').fetchone()[0] == '完全虚构，仅供隔离测试'
        assert ledger.execute('SELECT value FROM fictional_fixture').fetchone()[0] == '完全虚构，仅供隔离测试'
        return deepcopy(case.current_groups)

    @contextmanager
    def original_authorization(ws, als, after):
        case.calls.append('authorization')
        assert after == 1000
        if case.noise:
            print('fictional-private-configuration-value')
            print('fictional-private-configuration-value', file=sys.stderr)
            logging.error('fictional-private-configuration-value')
        if case.side_effect:
            try:
                case.side_effect()
            except Exception:
                pass  # 模拟原函数吞异常；外层仍必须失败。
        if case.failure:
            raise case.failure
        yield SimpleNamespace(wechat_rows=wechat_rows, alibaba_groups=alibaba_groups,
            core=fake_core(), auth={'owner': 'fictional-owner', 'binding_id': 'fictional-binding',
                                    'authority_sha': p.b.sha('fictional-authority')})
        case.calls.append('authorization_rechecked')

    monkeypatch.setattr(r.Backend, 'provenance', staticmethod(provenance))
    monkeypatch.setattr(r.Backend, 'candidates', staticmethod(candidates))
    monkeypatch.setattr(r, 'original_authorization', original_authorization)
    with sqlite3.connect(case.root / 'snapshot/wechat/catalog.sqlite3') as db:
        db.executescript('''CREATE TABLE channel_business_evidence(
            evidence_id TEXT,body_text TEXT,source_sha256 TEXT,owner TEXT,source_scope TEXT,message_id TEXT);
            CREATE TABLE kb_segment(unit_id TEXT,unit_sha256 TEXT,status TEXT);
            CREATE TABLE kb_learning_segment(unit_id TEXT,unit_sha256 TEXT,status TEXT);''')
        for row in rows:
            db.execute('INSERT INTO channel_business_evidence VALUES(?,?,?,?,?,?)',
                       tuple(row[field] for field in ('evidence_id', 'body_text', 'source_sha256',
                             'owner', 'source_scope', 'message_id')))
    with sqlite3.connect(case.root / 'snapshot/alibaba/inbound.sqlite3') as db:
        db.execute('CREATE TABLE inbound(id TEXT,payload TEXT)')
        payload = {key: record[key] for key in ('account', 'conversation_id', 'message_id')}
        payload['content'] = record['body']
        db.execute('INSERT INTO inbound VALUES(?,?)', (record['inbound_id'], p.b.canon(payload)))
    with sqlite3.connect(case.root / 'snapshot/alibaba/analysis.sqlite3') as db:
        db.executescript('''CREATE TABLE windows(version TEXT,window_index INTEGER,plan_json TEXT);
            CREATE TABLE analyses(version TEXT,group_json TEXT);
            CREATE TABLE model_attempts(version TEXT,window_index INTEGER);''')
    manifest = p.read_json(case.root / 'snapshot/snapshots.json')
    for name, item in manifest['files'].items():
        item['sha256'] = p.sha(p.raw_file(case.root / 'snapshot' / name))
    case.files = deepcopy(manifest['files'])
    (case.root / 'snapshot/snapshots.json').unlink()
    p.write_json(case.root / 'snapshot/snapshots.json', manifest)
    stage_marker(case.root, 'snapshot')
    return case


def generate(case, monkeypatch):
    with tmp_only(monkeypatch) as opened:
        result = r.generate(case.root, 1000, writers_stopped=True)
    assert opened and all(Path(path).is_relative_to('/tmp') for path in opened)
    output = Path(result['私有目录'])
    return result, output, p.read_json(output / 'review.json')


def update_inventory(case):
    case.provenance = provenance_fixture(case.quarantine)
    for name, value in (('quarantine.json', case.quarantine), ('provenance.json', case.provenance)):
        path = case.root / 'inventory' / name
        path.unlink()
        p.write_json(path, value)
    stage_marker(case.root, 'inventory')


def refresh_snapshot(case):
    path = case.root / 'snapshot/snapshots.json'
    manifest = p.read_json(path)
    for name, item in manifest['files'].items():
        item['sha256'] = p.sha(p.raw_file(case.root / 'snapshot' / name))
    case.files = deepcopy(manifest['files'])
    path.unlink()
    p.write_json(path, manifest)
    stage_marker(case.root, 'snapshot')


def test_all_candidates_pass_original_authorization_without_real_file_reads(fictional, monkeypatch, capsys):
    before = {path: path.read_bytes() for path in (fictional.root / 'snapshot').rglob('*') if path.is_file()}
    result, output, review = generate(fictional, monkeypatch)
    assert set(review) == {'schema', 'after', 'snapshots', 'entries'}
    expected_pins = {name.replace('/', '_').replace('.sqlite3', '').replace('.json', ''): item['sha256']
                     for name, item in fictional.files.items()}
    assert len(expected_pins) == 11 and review['snapshots'] == expected_pins and review['after'] == 1000
    assert len(review['entries']) == 3
    assert {entry['channel'] for entry in review['entries']} == {'wechat', 'alibaba'}
    assert result['review_sha256'] == p.sha(p.raw_file(output / 'review.json'))
    p.b.validate_review(review, fictional.quarantine)
    assert fictional.calls[-1] == 'authorization_rechecked'
    assert before == {path: path.read_bytes() for path in before}
    for path in (output, *output.rglob('*')):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    public = p.b.canon(result)
    assert 'fictional-' not in public and '完全虚构' not in public
    assert capsys.readouterr().out == ''


def test_partial_denial_stays_in_private_exclusions(fictional, monkeypatch):
    fictional.current_rows = fictional.current_rows[1:]
    result, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert all(entry['source_id'] != 'KL:fictional-source-0' for entry in review['entries'])
    excluded = p.read_json(output / 'exclusions.json')
    assert excluded and 'fictional-source-0' not in p.b.canon(excluded)
    assert result['排除候选数'] == 1


def test_parent_version_change_cannot_bypass_original_alibaba_freeze(fictional, monkeypatch):
    group = next(iter(fictional.current_groups.values()))
    group['source_version'] = p.b.sha('fictional-current-parent-changed')
    _, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert 'SOURCE_CHANGED' in p.b.canon(p.read_json(output / 'exclusions.json'))


def test_role_conflict_does_not_reuse_old_supplier_authority(fictional, monkeypatch):
    fictional.current_rows[0]['role'] = 'learning'
    fictional.current_rows[0]['channel_role'] = 'CHANNEL_ROLE_NEUTRAL'
    fictional.learning[fictional.current_rows[0]['chatroom_id']] = {'registered_business_role': 'learning'}
    _, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert 'ROLE' in p.b.canon(p.read_json(output / 'exclusions.json'))


def test_snapshot_hashed_room_matches_original_authorization_raw_room(fictional, monkeypatch):
    candidate = fictional.wechat[0]
    candidate['row']['chatroom_id'] = p.b.sha(fictional.current_rows[0]['chatroom_id'])
    candidate['units'] = [stable_unit(fictional.extract, candidate['row'])]
    _, _, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 3
    from production_adapter import wechat_entry
    expected = wechat_entry(fictional.extract, candidate['units'][0], [fictional.current_rows[0]])
    assert expected in review['entries']


@pytest.mark.parametrize('channel', ['wechat', 'alibaba'])
def test_platform_message_alias_hits_unknown_despite_new_id_body_and_sha(fictional, monkeypatch, channel):
    if channel == 'wechat':
        row = fictional.wechat[0]['row']
        with sqlite3.connect(fictional.root / 'snapshot/wechat/catalog.sqlite3') as db:
            db.execute('INSERT INTO channel_business_evidence VALUES(?,?,?,?,?,?)', (
                'fictional-held-source-0', '虚构别名旧正文，与候选完全不同', p.b.sha('fictional-alias-original'),
                row['owner'], row['source_scope'], row['message_id']))
    else:
        record = fictional.alibaba[0]['record']
        payload = {key: record[key] for key in ('account', 'conversation_id', 'message_id')}
        payload['content'] = '虚构别名旧正文，与候选完全不同'
        with sqlite3.connect(fictional.root / 'snapshot/alibaba/inbound.sqlite3') as db:
            db.execute('INSERT INTO inbound VALUES(?,?)', ('fictional-held-source-3', p.b.canon(payload)))
    refresh_snapshot(fictional)
    _, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert 'UNKNOWN_CROSS' in p.b.canon(p.read_json(output / 'exclusions.json'))


@pytest.mark.parametrize('channel', ['wechat', 'alibaba'])
@pytest.mark.parametrize('match', ['source_keys', 'source_sha256', 'content_sha256', 'prompt_sha256'])
def test_four_unknowns_exclude_aliases_across_both_channels(fictional, monkeypatch, channel, match):
    from production_adapter import single_alibaba_unit, wechat_prompt
    if channel == 'wechat':
        candidate = fictional.wechat[0]
        row, unit = candidate['row'], candidate['units'][0]
        values = {'source_keys': p.b.sha(row['evidence_id']), 'source_sha256': row['source_sha256'],
                  'content_sha256': p.b.sha(row['body_text']),
                  'prompt_sha256': p.b.sha(wechat_prompt(fictional.extract, unit))}
    else:
        candidate = fictional.alibaba[0]
        record = candidate['record']
        unit = single_alibaba_unit(fake_core(), candidate['group'], record)
        values = {'source_keys': p.b.sha(record['inbound_id']), 'source_sha256': record['source_sha256'],
                  'content_sha256': p.b.sha(record['body']), 'prompt_sha256': unit['prompt_sha256']}
    # 命中另一渠道的 hold 也应排除，不能借换渠道绕过。
    hold = fictional.quarantine['holds'][3 if channel == 'wechat' else 0]
    hold[match] = values[match] if match == 'prompt_sha256' else [values[match]]
    update_inventory(fictional)
    _, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert 'UNKNOWN' in p.b.canon(p.read_json(output / 'exclusions.json'))


def test_missing_authorization_configuration_stops_without_fallback(fictional, monkeypatch):
    fictional.failure = FileNotFoundError('fictional-secret-configuration-value')
    with tmp_only(monkeypatch), pytest.raises((ValueError, RuntimeError, OSError)):
        r.generate(fictional.root, 1000, writers_stopped=True)
    assert fictional.calls.count('authorization') == 1
    assert 'authorization_rechecked' not in fictional.calls


@pytest.mark.parametrize('failure', [ImportError('fictional-private-module-value'),
                                    RuntimeError('fictional-private-configuration-value')])
def test_cli_only_reports_error_code_and_never_retries(fictional, monkeypatch, capsys, failure):
    fictional.failure = failure
    with tmp_only(monkeypatch):
        code = r.main(['--session', str(fictional.root), '--after', '1000', '--writers-stopped'])
    assert code == 1 and fictional.calls.count('authorization') == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err) == {'错误代码': 'AUTH_REVIEW_STOPPED'}
    assert 'fictional-private' not in captured.err


def test_original_module_stdout_stderr_and_logging_do_not_escape(fictional, monkeypatch, capsys):
    fictional.noise = True
    _, output, _ = generate(fictional, monkeypatch)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''
    assert all('fictional-private-configuration-value' not in path.read_text()
               for path in output.iterdir())


def test_swallowed_write_attempt_still_stops_review(fictional, monkeypatch, tmp_path):
    target = tmp_path / 'fictional-forbidden-write'
    fictional.side_effect = lambda: target.write_text('fictional-private-configuration-value')
    with tmp_only(monkeypatch), pytest.raises((ValueError, RuntimeError, OSError)):
        r.generate(fictional.root, 1000, writers_stopped=True)
    assert not target.exists()


def test_existing_unit_is_not_put_into_new_request_list(fictional, monkeypatch):
    unit = fictional.wechat[0]['units'][0]
    with sqlite3.connect(fictional.root / 'snapshot/wechat/catalog.sqlite3') as db:
        db.execute('INSERT INTO kb_segment VALUES(?,?,?)',
                   (unit['unit_id'], unit['unit_sha256'], 'failed'))
    refresh_snapshot(fictional)
    _, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert 'PRIOR_UNIT_REVIEW_REQUIRED' in p.b.canon(p.read_json(output / 'exclusions.json'))


def test_old_alibaba_attempt_is_not_reissued_as_single_source(fictional, monkeypatch):
    candidate = fictional.alibaba[0]
    record = candidate['record']
    with sqlite3.connect(fictional.root / 'snapshot/alibaba/analysis.sqlite3') as db:
        db.execute('INSERT INTO analyses VALUES(?,?)', ('fictional-old-version', p.b.canon(candidate['group'])))
        db.execute('INSERT INTO windows VALUES(?,?,?)', ('fictional-old-version', 0, p.b.canon({
            'segments': [{'inbound_id': record['inbound_id'], 'source_sha256': record['source_sha256']}]})))
        db.execute('INSERT INTO model_attempts VALUES(?,?)', ('fictional-old-version', 0))
    refresh_snapshot(fictional)
    _, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert 'PRIOR_REQUEST_REVIEW_REQUIRED' in p.b.canon(p.read_json(output / 'exclusions.json'))


def test_customer_inquiry_is_excluded_even_if_fake_authorization_returns_it(fictional, monkeypatch):
    fictional.current_rows[0]['price_kind'] = 'customer_inquiry'
    fictional.wechat[0]['row']['price_kind'] = 'customer_inquiry'
    _, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert 'SOURCE_NOT_ELIGIBLE' in p.b.canon(p.read_json(output / 'exclusions.json'))


def test_natural_increment_is_left_out_of_backlog_review(fictional, monkeypatch):
    for row in (fictional.current_rows[0], fictional.wechat[0]['row']):
        row['source_created_at'], row['created_at'] = 1100, 1101
    fictional.wechat[0]['units'] = [stable_unit(fictional.extract, fictional.wechat[0]['row'])]
    _, output, review = generate(fictional, monkeypatch)
    assert len(review['entries']) == 2
    assert 'NATURAL_INCREMENT' in p.b.canon(p.read_json(output / 'exclusions.json'))


@pytest.mark.parametrize('path', ['wechat/catalog.sqlite3', 'wechat/usage.json', 'alibaba/ledger.sqlite3'])
def test_snapshot_hash_tampering_stops_before_authorization(fictional, monkeypatch, path):
    target = fictional.root / 'snapshot' / path
    with target.open('ab') as stream:
        stream.write(b'fictional-tampering')
    with tmp_only(monkeypatch), pytest.raises((ValueError, RuntimeError, OSError)):
        r.generate(fictional.root, 1000, writers_stopped=True)
    assert 'authorization' not in fictional.calls


def test_unknown_checklist_must_bind_exact_quarantine(fictional, monkeypatch):
    fictional.provenance['unknown_checklist'][0]['source_set_sha256'] = 'f' * 64
    with tmp_only(monkeypatch), pytest.raises((ValueError, RuntimeError, OSError)):
        r.generate(fictional.root, 1000, writers_stopped=True)
    assert 'authorization' not in fictional.calls


def test_writers_stopped_acknowledgment_is_required(fictional, monkeypatch):
    with tmp_only(monkeypatch), pytest.raises((ValueError, RuntimeError, OSError)):
        r.generate(fictional.root, 1000)
    assert 'authorization' not in fictional.calls


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'public'])
def test_snapshot_file_type_and_permissions_are_checked(fictional, monkeypatch, tmp_path, kind):
    target = fictional.root / 'snapshot/wechat/catalog.sqlite3'
    if kind == 'public':
        target.chmod(0o644)
    else:
        original = tmp_path / 'fictional-original.sqlite3'
        target.rename(original)
        if kind == 'symlink':
            target.symlink_to(original)
        else:
            import os
            os.link(original, target)
    with tmp_only(monkeypatch), pytest.raises((ValueError, RuntimeError, OSError)):
        r.generate(fictional.root, 1000, writers_stopped=True)
    assert 'authorization' not in fictional.calls


def test_review_can_be_frozen_by_existing_prepare_install_plan(fictional, monkeypatch, tmp_path):
    result, output, review = generate(fictional, monkeypatch)
    draft = p.private_dir(tmp_path, 'fictional-draft')
    for name in p.RELEASE:
        p.write_json(draft / name, {'fictional': True})
    backend = SimpleNamespace(draft=lambda ws, after: draft)
    session = p.Session(fictional.root, backend=backend)
    with tmp_only(monkeypatch):
        session.run('plan', after=1000, review_path=output / 'review.json',
                    review_sha256=result['review_sha256'])
    manifest = p.read_json(fictional.root / 'plan/manifest.json')
    p.b.validate_manifest(manifest, p.b.digest(manifest), fictional.quarantine)
    assert len(manifest['items']) == 3 and manifest['snapshots'] == review['snapshots']


def test_tmp_guard_rejects_production_files_and_databases(monkeypatch):
    with tmp_only(monkeypatch):
        with pytest.raises(AssertionError, match='真实路径'):
            Path('/var/lib/imgw/fictional-forbidden').read_bytes()
        with pytest.raises(AssertionError, match='真实路径'):
            sqlite3.connect('file:/var/lib/imgw/fictional-forbidden.sqlite3?mode=ro', uri=True)


@pytest.mark.parametrize('exclude', [None, 'customer_inquiry', 'old_admission', 'unknown'])
def test_real_candidate_collector_reads_only_virtual_snapshot_schema(fictional, monkeypatch, exclude):
    ws, als = fictional.root / 'snapshot/wechat', fictional.root / 'snapshot/alibaba'
    with sqlite3.connect(ws / 'catalog.sqlite3') as db:
        for name in ('classification_id', 'source_created_at', 'received_at', 'created_at',
                     'source_kind', 'price_kind', 'is_cost_price'):
            db.execute('ALTER TABLE channel_business_evidence ADD COLUMN ' + name)
        db.execute('ALTER TABLE kb_segment ADD COLUMN chatroom_id')
        db.execute('ALTER TABLE kb_learning_segment ADD COLUMN chatroom_id')
        db.execute('''CREATE TABLE channel_role_registry(classification_id,owner,source_scope,
            channel_kind,channel_role,channel_id_sha256)''')
        for number, source in enumerate(fictional.wechat):
            row = source['row']
            class_id = 'fictional-class-' + str(number)
            db.execute('''UPDATE channel_business_evidence SET classification_id=?,
                source_created_at=?,received_at=?,created_at=?,source_kind=?,price_kind=?,is_cost_price=?
                WHERE evidence_id=?''', (class_id, row['source_created_at'], row['received_at'],
                    row['created_at'], row['source_kind'], row['price_kind'], row['is_cost_price'], row['evidence_id']))
            db.execute('INSERT INTO channel_role_registry VALUES(?,?,?,?,?,?)', (
                class_id, row['owner'], row['source_scope'], 'group', 'CHANNEL_ROLE_SUPPLIER',
                p.b.sha(row['chatroom_id'])))
            db.execute('INSERT INTO kb_segment VALUES(?,?,?,?)', (
                'fictional-old-unit-' + str(number), p.b.sha('fictional-old-unit-' + str(number)),
                'done', row['chatroom_id']))
        if exclude == 'customer_inquiry':
            db.execute('UPDATE channel_business_evidence SET price_kind=? WHERE evidence_id=?',
                       ('customer_inquiry', fictional.wechat[0]['row']['evidence_id']))
        elif exclude == 'old_admission':
            db.execute('UPDATE channel_business_evidence SET created_at=? WHERE evidence_id=?',
                       (99, fictional.wechat[0]['row']['evidence_id']))
        elif exclude == 'unknown':
            db.execute('UPDATE channel_business_evidence SET source_sha256=? WHERE evidence_id=?',
                       (fictional.quarantine['holds'][0]['source_sha256'][0],
                        fictional.wechat[0]['row']['evidence_id']))
    with sqlite3.connect(ws / 'calls.sqlite3') as db:
        db.execute('CREATE TABLE calls(created_at)')
        db.execute('INSERT INTO calls VALUES(100)')
    with sqlite3.connect(ws / 'state.sqlite3') as db:
        db.execute('CREATE TABLE channel_role_intake(observation_json)')
    candidate = fictional.alibaba[0]
    with sqlite3.connect(als / 'analysis.sqlite3') as db:
        db.execute('INSERT INTO analyses VALUES(?,?)', ('fictional-current', p.b.canon(candidate['group'])))
        db.execute('CREATE TABLE current_conversations(analysis_version)')
        db.execute('INSERT INTO current_conversations VALUES(?)', ('fictional-current',))
    monkeypatch.setattr(r.wechat, 'algorithms', lambda: vars(fictional.extract))
    monkeypatch.setattr(r.alibaba, 'analyze', lambda snapshot: ({}, {
        'backlog_review_template': {'entries': [{
            'source_id': candidate['record']['inbound_id'],
            'source_sha256': candidate['record']['source_sha256'],
            'content_sha256': p.b.sha(candidate['record']['body'])}]}}))
    with tmp_only(monkeypatch):
        wc, ac = ORIGINAL_CANDIDATES(r.Backend(), ws, als, fictional.quarantine)
    assert len(wc) == (2 if exclude is None else 1) and len(ac) == 1
    for item in wc:
        original = next(row for row in fictional.current_rows if row['evidence_id'] == item['row']['evidence_id'])
        assert item['row']['chatroom_id'] == p.b.sha(original['chatroom_id'])
        assert item['units'][0]['unit_id'] == 'KL:' + original['evidence_id']
        assert item['units'][0]['business_role'] == 'supplier'
    if exclude is None:
        for number, row in enumerate(fictional.current_rows):
            row['classification_id'] = 'fictional-class-' + str(number)
        refresh_snapshot(fictional)
        monkeypatch.setattr(r.Backend, 'candidates', ORIGINAL_CANDIDATES)
        _, _, review = generate(fictional, monkeypatch)
        assert len(review['entries']) == 3
