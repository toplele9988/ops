"""Resolve self-contained fixture references and check every output field."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import pytest
from console.cpq import compute
from .engine_helpers import inputs_from_dict

DOCUMENT = json.loads((Path(__file__).parent / 'fixtures' / 'cpq_vectors.json').read_text(encoding='utf-8'))


def _resolve(value: Any, trail: tuple[str, ...] = ()) -> Any:
    if isinstance(value, list):
        return [_resolve(v, trail) for v in value]
    if not isinstance(value, dict):
        return value
    base: dict[str, Any] = {}
    if '$ref' in value:
        reference = value['$ref']
        assert reference.startswith('#/') and reference not in trail
        target: Any = DOCUMENT
        for key in reference[2:].split('/'):
            target = target[key]
        base = _resolve(target, (*trail, reference))
    base.update({k: _resolve(v, trail) for k, v in value.items() if k != '$ref'})
    return base


def _record(name: str, values: list[Any]) -> dict[str, Any]:
    columns = DOCUMENT['columns'][name]
    assert len(columns) == len(values), name
    return dict(zip(columns, values))


def _expected(values: list[Any]) -> dict[str, Any]:
    result = _record('result', values)
    result['lines'] = [_record('line', line) for line in result['lines']]
    result['totals'] = _record('totals', result['totals'])
    result['breakdown'] = _record('breakdown', result['breakdown'])
    result['packing_compare'] = {key: _record('comparison', value) for key, value in zip(('carton', 'flight_case'), result['packing_compare'])}
    result['gate'] = _record('gate', result['gate'])
    return result


def _equal(actual: Any, expected: Any, path: str = '$') -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys(), path
        for key in expected:
            _equal(actual[key], expected[key], f'{path}.{key}')
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for index, (a, e) in enumerate(zip(actual, expected)):
            _equal(a, e, f'{path}[{index}]')
    elif isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert actual == expected, path
    else:
        assert abs(actual - expected) <= 0.01, path


@pytest.mark.parametrize('vector', DOCUMENT['vectors'], ids=lambda v: v['id'])
def test_golden_vectors(vector: dict[str, Any]) -> None:
    assert vector['note']
    _equal(compute(inputs_from_dict(_resolve(vector['input']))).to_dict(), _expected(vector['expected']))


def test_vector_coverage() -> None:
    assert len(DOCUMENT['vectors']) >= 12
    inputs = [_resolve(v['input']) for v in DOCUMENT['vectors']]
    combinations = {(v['shipping']['mode'], v['shipping']['incoterm']) for v in inputs}
    assert {(m, t) for m in ('sea_lcl', 'sea_fcl') for t in ('FOB', 'CIF', 'DDP')} | {('express', 'DAP'), ('express', 'DDP')} <= combinations
