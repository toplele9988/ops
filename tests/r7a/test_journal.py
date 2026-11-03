import json

import pytest

from conftest import ROOT, load_module

JOURNAL = load_module('r7a_journal', ROOT / 'deploy/r7a/journal_summary.py')


def test_journal_only_emits_fixed_codes_and_source_locations():
    private = '虚构正文、凭据和身份不得输出'
    lines = [json.dumps(dict(MESSAGE=json.dumps(dict(status='error', type='ValueError',
                error_code='binding_mismatch', content=private, authorization=private)))),
             json.dumps(dict(MESSAGE=f'File "/opt/imgw/waha-names-refresh-v1/private_history.py", line 51\nValueError: {private}')),
             json.dumps(dict(MESSAGE=f'File "/tmp/{private}.py", line 8\n{private}')),
             json.dumps(dict(MESSAGE=json.dumps(dict(status=private, type=private, error_code=private))))]
    result = JOURNAL.summarize(lines)
    assert result['error_codes'] == {'binding_mismatch': 1}
    assert result['types'] == {'ValueError': 1}
    assert result['frames'] == [dict(file='/opt/imgw/waha-names-refresh-v1/private_history.py', line=51, count=1)]
    assert private not in json.dumps(result, ensure_ascii=False)


def test_same_error_in_two_fields_counts_one_event():
    line = json.dumps(dict(MESSAGE=json.dumps(dict(error='index_unavailable', error_code='index_unavailable'))))
    assert JOURNAL.summarize([line])['error_codes'] == {'index_unavailable': 1}


@pytest.mark.parametrize('line', ['bad-json', '{}', '[]', '{"MESSAGE":null}', 'x'*262145])
def test_journal_invalid_input_does_not_echo(line):
    assert JOURNAL.summarize([line]) == dict(records=0, invalid=1, error_codes={}, states={}, types={}, frames=[])
