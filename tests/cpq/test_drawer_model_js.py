"""Run the pure drawer model contract tests with Node; missing Node is a failure."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).with_name('drawer_model_runner.mjs')


def run_case(name):
    node = shutil.which('node')
    if node is None:
        pytest.fail('Node.js is required for drawer model tests; node was not found on PATH')
    try:
        result = subprocess.run(
            [node, str(RUNNER), name],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f'Unable to execute drawer model test {name}: {exc}')
    assert result.returncode == 0, (
        f'{name} failed with exit code {result.returncode}\n'
        f'stdout:\n{result.stdout}\nstderr:\n{result.stderr}'
    )
    assert f'PASS {name}' in result.stdout


def test_action_states_fuse_blocks_all():
    run_case('test_action_states_fuse_blocks_all')


def test_action_states_ack_and_override():
    run_case('test_action_states_ack_and_override')


def test_slider_zones_and_margin_zone():
    run_case('test_slider_zones_and_margin_zone')


def test_freight_level_bands():
    run_case('test_freight_level_bands')


def test_incoterm_rules():
    run_case('test_incoterm_rules')


def test_lane_state():
    run_case('test_lane_state')


def test_build_calc_request_guards():
    run_case('test_build_calc_request_guards')


def test_cost_series_masked():
    run_case('test_cost_series_masked')


def test_model_has_no_dom_or_business_literals():
    run_case('test_model_has_no_dom_or_business_literals')
