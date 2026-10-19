import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope='module')
def basket_results():
    node = shutil.which('node')
    assert node is not None, 'Node.js is required for basket tests; skipping is not allowed'
    runner = Path(__file__).resolve().with_name('basket_runner.mjs')
    completed = subprocess.run(
        [node, str(runner)],
        cwd=runner.parents[2],
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, (
        f'Basket runner failed ({completed.returncode})\n'
        f'stdout: {completed.stdout}\nstderr: {completed.stderr}'
    )
    results = json.loads(completed.stdout)
    assert set(results) == {
        'test_basket_merge_quantity',
        'test_basket_rejects_bad_qty',
        'test_basket_request_has_no_cost_keys',
        'test_basket_survives_storage_failure',
    }
    return results


def test_basket_merge_quantity(basket_results):
    result = basket_results['test_basket_merge_quantity']
    assert result['ok'] is True, result
    assert result['line_count'] == 1
    assert result['qty'] == result['count'] == 5


def test_basket_rejects_bad_qty(basket_results):
    result = basket_results['test_basket_rejects_bad_qty']
    assert result['ok'] is True, result
    assert result['rejected'] == [0, -1, 1.5, '3']


def test_basket_request_has_no_cost_keys(basket_results):
    result = basket_results['test_basket_request_has_no_cost_keys']
    assert result['ok'] is True, result
    request = result['request']
    assert set(request) == {'lines', 'shipping', 'target_margin', 'pricing_mode'}
    assert set(request['shipping']) == {'mode', 'incoterm', 'lane_id'}
    assert request['shipping']['lane_id']
    assert request['lines']
    for line in request['lines']:
        assert set(line) == {
            'model_id', 'qty', 'packing_mode', 'packing_override',
            'accessories', 'unit_price_usd',
        }
        assert line['packing_override'] is None
        assert type(line['qty']) is int and line['qty'] > 0
    pending = [request]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                assert not any(word in key.lower() for word in ('cost', 'exw', 'supplier', 'offer'))
                pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)


def test_basket_survives_storage_failure(basket_results):
    result = basket_results['test_basket_survives_storage_failure']
    assert result['ok'] is True, result
    assert result['reads'] == 1
    assert result['writes'] > 0
    assert result['request']['lines'][0]['qty'] == 2
