"""Compare every serialized JavaScript result field with the Python engine."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

import pytest

from console.cpq.engine import compute
from .engine_helpers import inputs_from_dict

pytestmark = pytest.mark.js

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'src' / 'console' / 'static' / 'js' / 'cpq' / 'cpq-engine.js'
RUNNER = Path(__file__).with_name('js_parity_runner.mjs')
VECTORS = Path(__file__).parent / 'fixtures' / 'cpq_vectors.json'


def _node() -> str:
    executable = shutil.which('node')
    assert executable is not None, 'Node.js >= 18 is required; node was not found on PATH'
    return executable


def _resolve(value: Any, fixture: dict[str, Any], trail: tuple[str, ...] = ()) -> Any:
    if isinstance(value, list):
        return [_resolve(item, fixture, trail) for item in value]
    if not isinstance(value, dict):
        return value
    base: dict[str, Any] = {}
    if '$ref' in value:
        reference = value['$ref']
        assert reference.startswith('#/') and reference not in trail
        target: Any = fixture
        for key in reference[2:].split('/'):
            target = target[key]
        base = _resolve(target, fixture, (*trail, reference))
    base.update({key: _resolve(item, fixture, trail) for key, item in value.items() if key != '$ref'})
    return base


def _equal(actual: Any, expected: Any, path: str = '$') -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f'{path}: expected an object, got {actual!r}'
        assert actual.keys() == expected.keys(), f'{path}: different keys: {actual.keys()} != {expected.keys()}'
        for key in expected:
            _equal(actual[key], expected[key], f'{path}.{key}')
    elif isinstance(expected, list):
        assert isinstance(actual, list), f'{path}: expected an array, got {actual!r}'
        assert len(actual) == len(expected), f'{path}: different array lengths'
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            _equal(actual_item, expected_item, f'{path}[{index}]')
    elif isinstance(expected, bool):
        assert type(actual) is bool and actual is expected, f'{path}: {actual!r} != {expected!r}'
    elif expected is None:
        assert actual is None, f'{path}: {actual!r} != None'
    elif isinstance(expected, str):
        assert isinstance(actual, str) and actual == expected, f'{path}: {actual!r} != {expected!r}'
    else:
        assert isinstance(expected, (int, float)), f'{path}: unsupported expected type'
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool), f'{path}: expected a number, got {actual!r}'
        assert abs(actual - expected) <= 0.01, f'{path}: {actual!r} != {expected!r} (tolerance 0.01)'


def test_js_engine_matches_python() -> None:
    fixture = json.loads(VECTORS.read_text(encoding='utf-8'))
    vectors = fixture['vectors']
    assert len(vectors) >= 12
    names = [vector['id'] for vector in vectors]
    assert len(set(names)) == len(names), 'Vector ids must be unique'
    completed = subprocess.run(
        [_node(), str(RUNNER), str(VECTORS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, (
        f'JavaScript runner failed ({completed.returncode})\n'
        f'stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}'
    )
    actual = json.loads(completed.stdout)
    assert isinstance(actual, dict)
    assert actual.keys() == set(names), 'Runner returned a different set of vector ids'
    for vector in vectors:
        inputs = inputs_from_dict(_resolve(vector['input'], fixture))
        expected = compute(inputs).to_dict()
        _equal(actual[vector['id']], expected, f"$[{vector['id']!r}]")


def test_js_engine_has_no_dom_access() -> None:
    source = ENGINE.read_text(encoding='utf-8')
    for forbidden in ('document', 'window', 'fetch(', 'localStorage', 'XMLHttpRequest'):
        assert forbidden not in source, f'Forbidden substring in cpq-engine.js: {forbidden}'


def test_node_available() -> None:
    completed = subprocess.run(
        [_node(), '--version'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, f'node --version failed: {completed.stderr}'
    version = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?', completed.stdout.strip())
    assert version is not None, f'Unexpected Node.js version: {completed.stdout!r}'
    assert int(version.group(1)) >= 18, f'Node.js >= 18 is required, got {completed.stdout.strip()}'
