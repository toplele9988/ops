"""Use disposable database copies and a local fake Sol command only."""
from copy import deepcopy
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import json
import shlex
import subprocess
import sys

import pytest
from starlette.applications import Starlette

from console.cpq import export, leak_guard, letter
from console.cpq.dto import CustomerQuoteDTO, dataclass_field_tree, round2
from console.routes.letter_routes import build_letter_routes
from console.routes.strobe_routes import build_routes
from console.strobe.repository import SUPPLIER_LEGAL_NAME


ROOT = Path(__file__).resolve().parents[2]
OPTIONS = {'tone': 'formal', 'purpose': 'first_quote', 'extra_notes': ''}
FAKE_SOL = r'''import json
import os
from pathlib import Path
import sys

payload = json.loads(sys.stdin.read())
Path(os.environ['LETTER_TEST_CAPTURE']).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
log = Path(os.environ['LETTER_TEST_CALLS'])
with log.open('a', encoding='utf-8') as stream:
    stream.write('call\n')
count = len(log.read_text(encoding='utf-8').splitlines())
scenario = os.environ.get('LETTER_TEST_SCENARIO', 'ok')
dto = payload['dto']
result = {
    'subject': 'Quotation ' + dto['quote_no'],
    'body': 'Please review quotation ' + dto['quote_no'] + '.\nKind regards,\n' + dto['seller']['contact'],
}
if scenario == 'mismatch' or (scenario == 'recover' and count == 1):
    result['body'] = 'Please review the price of USD 159.00.'
elif scenario == 'leak':
    result['body'] = 'Please contact ' + os.environ['LETTER_TEST_SUPPLIER'] + '.'
elif scenario == 'long':
    result['body'] = 'review ' * 251
elif scenario == 'empty':
    result['body'] = '   '
elif scenario == 'malformed':
    sys.stdout.write('{')
    raise SystemExit(0)
elif scenario == 'nonzero':
    sys.stderr.write('private-process-diagnostic')
    raise SystemExit(7)
print(json.dumps(result, ensure_ascii=False))
'''


@pytest.fixture
def app(settings):
    routes = build_routes(settings)
    paths = {route.path for route in routes}
    routes.extend(route for route in build_letter_routes(settings) if route.path not in paths)
    result = Starlette(routes=routes)
    result.state.strobe_api = next(r for r in routes if r.name == 'strobe_list').endpoint.__self__
    result.state.strobe_repository = result.state.strobe_api.repository
    result.state.cpq_service = next(r for r in routes if r.name == 'cpq_context').endpoint.__self__.service
    return result


@pytest.fixture
def quotation(manager, seeded, app):
    candidates = [mid for mid in seeded.model_ids
                  if (item := app.state.cpq_service.repository.get_strobe(mid))['dmx_modes']
                  and item['power_w'] and item['images']]
    assert candidates, 'Real seeded data must provide DMX, Power and images'
    payload = deepcopy(seeded.payload)
    payload['lines'][0]['model_id'] = candidates[0]
    payload['target_margin'] = 0.20
    calculated = manager.post('/api/cpq/calculate', json=payload)
    assert calculated.status_code == 200, calculated.text
    # Synthetic delivery interval, matching the supplied export fixture.
    payload.update(acks=calculated.json()['gate']['requires_ack'],
                   payment_terms_id='TT_30_70', lead_time_days={candidates[0]: '15-20'})
    response = manager.post('/api/cpq/quotes', json=payload)
    assert response.status_code == 201, response.text
    quote = response.json()
    row = seeded.store.read('SELECT customer_dto_json FROM cpq_quote WHERE quote_token=?',
                            (quote['quote_token'],))[0]
    return quote, json.loads(row['customer_dto_json'])


@pytest.fixture
def fake_sol(tmp_path, monkeypatch):
    script = tmp_path / 'fake sol.py'
    script.write_text(FAKE_SOL, encoding='utf-8')
    capture, calls = tmp_path / 'payload.json', tmp_path / 'calls.txt'
    monkeypatch.setenv('SOL_LETTER_CMD', shlex.join([sys.executable, str(script)]))
    monkeypatch.setenv('LETTER_TEST_CAPTURE', str(capture))
    monkeypatch.setenv('LETTER_TEST_CALLS', str(calls))
    monkeypatch.setenv('LETTER_TEST_SCENARIO', 'ok')
    monkeypatch.setenv('LETTER_TEST_SUPPLIER', SUPPLIER_LEGAL_NAME)
    return SimpleNamespace(capture=capture, calls=calls)


def call_count(fake):
    return len(fake.calls.read_text(encoding='utf-8').splitlines()) if fake.calls.exists() else 0


def post_letter(client, quote, **changes):
    return client.post(f"/api/quotes/{quote['quote_token']}/letter", json=OPTIONS | changes)


def without_images(value):
    if isinstance(value, dict):
        return {key: without_images(child) for key, child in value.items() if key != 'image_refs'}
    if isinstance(value, list):
        return [without_images(child) for child in value]
    return value


def all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_keys(child)


def loaded_dto(quotation):
    return export.load_customer_dto(json.dumps(quotation[1], ensure_ascii=False))


def stored_letters(seeded, quote):
    return seeded.store.read('SELECT * FROM cpq_quote_letter WHERE quote_token=? ORDER BY version',
                             (quote['quote_token'],))


def test_letter_payload_is_dto_only(manager, quotation, fake_sol):
    quote, dto = quotation
    response = post_letter(manager, quote)
    assert response.status_code == 201, response.text
    payload = json.loads(fake_sol.capture.read_text(encoding='utf-8'))
    # The required wire envelope wraps the DTO. Apply the DTO field whitelist
    # inside that envelope and separately verify its three control fields.
    assert set(payload) == {'dto', 'tone', 'purpose', 'extra_notes'}
    assert payload['dto'] == without_images(dto)
    logical_fields = set(payload['dto']) | (set(payload) - {'dto'})
    assert logical_fields <= set(dataclass_field_tree(CustomerQuoteDTO)) | {
        'tone', 'purpose', 'extra_notes',
    }
    keys = list(all_keys(payload))
    assert 'image_refs' not in keys and 'internal_snapshot' not in keys
    assert all(not any(word in key.casefold() for word in ('cost', 'exw', 'supplier', 'offer', 'margin'))
               for key in keys)
    assert call_count(fake_sol) == 1


def test_letter_number_mismatch_rejected(manager, quotation, seeded, fake_sol, monkeypatch):
    quote, _ = quotation
    assert Decimal('159.00') not in letter.allowed_numbers(loaded_dto(quotation))
    monkeypatch.setenv('LETTER_TEST_SCENARIO', 'mismatch')
    response = post_letter(manager, quote)
    assert response.status_code == 422, response.text
    problem = response.json()['error']
    assert problem['code'] == 'LETTER_NUMBER_MISMATCH'
    assert set(problem['details']) == {'numbers'}
    assert [Decimal(value) for value in problem['details']['numbers']] == [Decimal('159.00')]
    assert call_count(fake_sol) == 2 and stored_letters(seeded, quote) == []
    audit = seeded.store.audit_log(quote['quote_token'])[-1]
    assert audit['action'] == 'LETTER_BLOCKED'
    assert audit['detail'] == {'code': problem['code'], **problem['details']}
    assert 'Please review the price' not in json.dumps(audit, ensure_ascii=False)


def test_letter_leak_rejected(manager, quotation, seeded, fake_sol, monkeypatch):
    quote, _ = quotation
    monkeypatch.setenv('LETTER_TEST_SCENARIO', 'leak')
    response = post_letter(manager, quote)
    assert response.status_code == 422, response.text
    problem = response.json()['error']
    assert problem['code'] == 'LEAK_BLOCKED'
    assert set(problem['details']) == {'categories'}
    assert 'supplier_name' in problem['details']['categories']
    assert SUPPLIER_LEGAL_NAME not in response.text
    assert call_count(fake_sol) == 2 and stored_letters(seeded, quote) == []
    audit = seeded.store.audit_log(quote['quote_token'])[-1]
    assert audit['action'] == 'LETTER_BLOCKED'
    assert set(audit['detail']) == {'code', 'categories'}
    assert SUPPLIER_LEGAL_NAME not in json.dumps(audit, ensure_ascii=False)


def test_letter_extra_notes_scanned(manager, quotation, seeded, fake_sol):
    quote, _ = quotation
    response = post_letter(manager, quote, extra_notes='出厂')
    assert response.status_code == 422, response.text
    assert response.json()['error'] == {
        'code': 'LEAK_BLOCKED', 'details': {'categories': ['internal_key']},
    }
    assert call_count(fake_sol) == 0 and not fake_sol.capture.exists()
    assert stored_letters(seeded, quote) == []
    audit = seeded.store.audit_log(quote['quote_token'])[-1]
    assert audit['action'] == 'LETTER_BLOCKED'
    assert '出厂' not in json.dumps(audit, ensure_ascii=False) and '出厂' not in response.text


def test_letter_stored_as_draft(manager, quotation, seeded, fake_sol):
    quote, _ = quotation
    first = post_letter(manager, quote)
    second = post_letter(manager, quote, tone='friendly', purpose='follow_up')
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()['version'] == 1 and second.json()['version'] == 2
    assert first.json()['status'] == second.json()['status'] == 'draft'
    assert set(first.json()) == {'quote_token', 'version', 'subject', 'body', 'status'}
    rows = stored_letters(seeded, quote)
    assert [row['version'] for row in rows] == [1, 2]
    assert all(row['status'] == 'draft' and row['created_by'] == 'test-manager'
               and row['created_at'] for row in rows)
    for row, response in zip(rows, (first, second), strict=True):
        assert row['subject'] == response.json()['subject']
        assert row['body'] == response.json()['body']
    history = manager.get(f"/api/quotes/{quote['quote_token']}/letters")
    assert history.status_code == 200, history.text
    assert history.json()['quote_token'] == quote['quote_token']
    assert [row['version'] for row in history.json()['letters']] == [1, 2]
    assert all(row['status'] == 'draft' for row in history.json()['letters'])
    assert history.headers['cache-control'] == 'private, no-store'
    assert call_count(fake_sol) == 2


def test_letter_requires_final_quote(manager, seeded, fake_sol):
    response = manager.post('/api/cpq/quotes',
                            json=deepcopy(seeded.payload) | {'draft': True, 'acks': []})
    assert response.status_code == 201, response.text
    result = post_letter(manager, response.json())
    assert result.status_code == 409
    assert result.json()['error']['code'] == 'QUOTE_NOT_FINAL'
    assert call_count(fake_sol) == 0


def test_letter_unavailable_without_env(manager, quotation, seeded, fake_sol, monkeypatch):
    monkeypatch.delenv('SOL_LETTER_CMD', raising=False)
    quote, _ = quotation
    response = post_letter(manager, quote)
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'LETTER_UNAVAILABLE'
    assert call_count(fake_sol) == 0 and stored_letters(seeded, quote) == []
    audit = seeded.store.audit_log(quote['quote_token'])[-1]
    assert audit['action'] == 'LETTER_FAILED'
    assert audit['detail'] == {'code': 'LETTER_UNAVAILABLE'}


def test_no_send_capability():
    forbidden = ('smtplib', 'sendmail', 'graph.facebook')
    for directory in (ROOT / 'src' / 'console', ROOT / 'scripts'):
        assert directory.is_dir()
        for path in directory.rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
                continue
            source = path.read_text(encoding='utf-8', errors='ignore').casefold()
            assert not any(value in source for value in forbidden), str(path)


def test_allowed_numbers_cover_dto_numbers(quotation):
    dto = loaded_dto(quotation)
    allowed = letter.allowed_numbers(dto)
    serialized = json.dumps(quotation[1], ensure_ascii=False, allow_nan=False)
    assert set(letter.extract_numbers(serialized)) <= allowed
    amounts = [dto.totals.total_amount]
    for line in dto.lines:
        amounts.extend((line.unit_price, line.amount))
    assert all(Decimal(f'{round2(value):.2f}') in allowed for value in amounts)
    assert {Decimal('30'), Decimal('70'), Decimal('15'), Decimal('20')} <= allowed


def test_extract_numbers_handles_thousands_and_decimals():
    # Synthetic parser examples, not customer business facts.
    text = 'USD 1,234.50; .75; 0.125; IP65; 30/70; 15-20; 2026-10-19; Q261019-001; -2.5; 1e-7'
    assert letter.extract_numbers(text) == [
        Decimal('1234.50'), Decimal('.75'), Decimal('0.125'), Decimal('65'),
        Decimal('30'), Decimal('70'), Decimal('15'), Decimal('20'),
        Decimal('2026'), Decimal('10'), Decimal('19'), Decimal('261019'),
        Decimal('001'), Decimal('-2.5'), Decimal('1e-7'),
    ]
    assert letter.extract_numbers('USD 1,234,567.89 and 12,000') == [
        Decimal('1234567.89'), Decimal('12000'),
    ]
    assert letter.extract_numbers('No numeric facts.') == []


def test_sol_wrapper_has_no_db_access():
    source = (ROOT / 'scripts' / 'sol_letter.py').read_text(encoding='utf-8').casefold()
    assert all(value.casefold() not in source for value in ('sqlite3', 'catalog', 'CPQ_WORK_DB'))


@pytest.mark.parametrize('scenario', ['long', 'empty', 'malformed', 'nonzero'])
def test_invalid_output_retries_and_fails(manager, quotation, seeded, fake_sol, monkeypatch, scenario):
    monkeypatch.setenv('LETTER_TEST_SCENARIO', scenario)
    quote, _ = quotation
    response = post_letter(manager, quote)
    assert response.status_code == 502, response.text
    assert response.json()['error']['code'] == 'LETTER_FAILED'
    assert call_count(fake_sol) == 2 and stored_letters(seeded, quote) == []
    assert 'private-process-diagnostic' not in response.text
    assert seeded.store.audit_log(quote['quote_token'])[-1]['action'] == 'LETTER_FAILED'


def test_retry_stores_only_valid_result(manager, quotation, seeded, fake_sol, monkeypatch):
    monkeypatch.setenv('LETTER_TEST_SCENARIO', 'recover')
    quote, _ = quotation
    assert Decimal('159.00') not in letter.allowed_numbers(loaded_dto(quotation))
    response = post_letter(manager, quote)
    assert response.status_code == 201, response.text
    assert response.json()['version'] == 1 and '159.00' not in response.json()['body']
    assert call_count(fake_sol) == 2
    rows = stored_letters(seeded, quote)
    assert len(rows) == 1 and rows[0]['body'] == response.json()['body']


def test_generate_letter_timeout_is_sanitized(quotation, fake_sol):
    attempts = []

    def timeout_run(command, **kwargs):
        attempts.append(command)
        assert kwargs['timeout'] == 90
        assert kwargs['capture_output'] is True and kwargs['text'] is True
        assert set(json.loads(kwargs['input'])) == {'dto', 'tone', 'purpose', 'extra_notes'}
        raise subprocess.TimeoutExpired(command, 90, output='private-process-diagnostic')

    with letter.inspection_context(leak_guard.LeakContext((), (), ())):
        with pytest.raises(letter.LetterError) as raised:
            letter.generate_letter(loaded_dto(quotation), 'formal', 'first_quote', '', run=timeout_run)
    assert raised.value.status_code == 502 and raised.value.code == 'LETTER_FAILED'
    assert raised.value.details is None and len(attempts) == 2
    assert call_count(fake_sol) == 0


def test_inspection_failure_is_closed(manager, quotation, fake_sol, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError('private-inspection-diagnostic')

    monkeypatch.setattr(leak_guard, 'load_leak_context', unavailable)
    response = post_letter(manager, quotation[0])
    assert response.status_code == 422
    assert response.json()['error'] == {
        'code': 'LEAK_BLOCKED', 'details': {'categories': ['inspection_error']},
    }
    assert call_count(fake_sol) == 0 and 'private-inspection-diagnostic' not in response.text


def test_letter_authorization_and_tokens(anon, viewer, manager, quotation, fake_sol):
    path = f"/api/quotes/{quotation[0]['quote_token']}"
    assert anon.post(path + '/letter', json=OPTIONS).status_code == 401
    assert anon.get(path + '/letters').status_code == 401
    assert viewer.post(path + '/letter', json=OPTIONS).status_code == 403
    assert viewer.get(path + '/letters').status_code == 403
    for token in ('invalid', 'q_' + 'A' * 32, 'q_' + '0' * 32):
        assert manager.post(f'/api/quotes/{token}/letter', json=OPTIONS).status_code == 404
        assert manager.get(f'/api/quotes/{token}/letters').status_code == 404
    assert call_count(fake_sol) == 0


def test_letter_request_validation(manager, quotation, fake_sol):
    path = f"/api/quotes/{quotation[0]['quote_token']}/letter"
    for body in ({}, OPTIONS | {'tone': 'urgent'}, OPTIONS | {'tone': []},
                 OPTIONS | {'purpose': 'other'}, OPTIONS | {'extra_notes': None},
                 OPTIONS | {'extra_notes': 'x' * 501}, OPTIONS | {'cost': 1}):
        assert manager.post(path, json=body).status_code == 400
    headers = {'content-type': 'application/json'}
    assert manager.post(path, content=b' ' * 8193, headers=headers).status_code == 413
    assert manager.post(path, content=(part for part in (b' ' * 4096, b' ' * 4097)),
                        headers=headers).status_code == 413
    duplicate = '{"tone":"formal","tone":"friendly","purpose":"first_quote"}'
    assert manager.post(path, content=duplicate, headers=headers).status_code == 400
    assert call_count(fake_sol) == 0


@pytest.fixture
def wrapper():
    spec = importlib.util.spec_from_file_location('cpq_sol_letter_wrapper_test', ROOT / 'scripts' / 'sol_letter.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dispatch_result(summary):
    return {'status': 'ok', 'degraded': False, 'served_by': {'model': 'gpt-6.1-sol'},
            'output': {'summary': summary}}


@pytest.mark.parametrize('summary', [
    '{"subject":"Review quotation","body":"Please review."}',
    '```json\n{"subject":"Review quotation","body":"Please review."}\n```',
    'Result:\n```json\n{"subject":"Review quotation","body":"Please review."}\n```',
    'Subject: Review quotation\nBody: Please review.',
    'Review quotation\nPlease review.',
])
def test_wrapper_summary_formats(wrapper, summary):
    assert wrapper.parse_summary(summary) == {'subject': 'Review quotation', 'body': 'Please review.'}


def test_wrapper_pins_dispatch_request(wrapper, quotation, monkeypatch, capsys):
    payload = {'dto': without_images(quotation[1]), **OPTIONS}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command == [sys.executable, '/opt/imgw/wechat/tools/swarm/swarm_dispatch.py', 'run']
        assert kwargs['timeout'] == 85
        assert kwargs['capture_output'] is True and kwargs['text'] is True
        request = json.loads(kwargs['input'])
        prompt = request.pop('task')
        assert request == {
            'backend': 'codex', 'data_class': 'code', 'attachments': [], 'repo_files': [],
            'media': [], 'frames': 8, 'tier': 'auto', 'fallback': False,
            'model': 'gpt-6.1-sol', 'effort': 'medium', 'timeout': 85,
        }
        assert prompt.startswith(letter.SYSTEM_PROMPT)
        assert prompt.endswith(json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True))
        response = dispatch_result('{"subject":"Review quotation","body":"Please review."}')
        return subprocess.CompletedProcess(command, 0, json.dumps(response), '')

    monkeypatch.setattr(wrapper.subprocess, 'run', run)
    monkeypatch.setattr(wrapper.sys, 'stdin', StringIO(json.dumps(payload)))
    assert wrapper.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'subject': 'Review quotation', 'body': 'Please review.'}
    assert captured.err == '' and len(calls) == 1


@pytest.mark.parametrize('change', [
    {'served_by': {'model': 'unexpected-model'}},
    {'served_by': {}},
    {'degraded': True},
    {'status': 'failed'},
])
def test_wrapper_rejects_wrong_service(wrapper, quotation, monkeypatch, capsys, change):
    response = dispatch_result('{"subject":"Review quotation","body":"Please review."}') | change

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(response), 'private-diagnostic')

    payload = {'dto': without_images(quotation[1]), **OPTIONS}
    monkeypatch.setattr(wrapper.subprocess, 'run', run)
    monkeypatch.setattr(wrapper.sys, 'stdin', StringIO(json.dumps(payload)))
    assert wrapper.main() == 2
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == 'LETTER_FAILED\n'
