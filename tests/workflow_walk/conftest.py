"""只加载走查脚本；所有业务数据和渠道均为虚构。"""
import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture(scope='session')
def workflow():
    path = Path(__file__).resolve().parents[2] / 'scripts/workflow_walk.py'
    spec = importlib.util.spec_from_file_location('workflow_walk', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
