"""Share fixtures explicitly: sibling conftest.py files are not inherited."""
from pathlib import Path
import importlib.util

_path = Path(__file__).resolve().parents[1] / 'cpq' / 'conftest.py'
_spec = importlib.util.spec_from_file_location('_strobe_shared_test_fixtures', _path)
if _spec is None or _spec.loader is None:
    raise RuntimeError('Cannot load shared strobe fixtures')
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)

snapshot_db = _shared.snapshot_db
work_db = _shared.work_db
settings = _shared.settings
app = _shared.app
viewer = _shared.viewer
sales_cost = _shared.sales_cost
