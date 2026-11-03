"""Separate processes avoid the old suites' unqualified conftest/test_service imports."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'private_out/r11/logs'
SUITES = sys.argv[1:] or ['inbox', 'parties', 'ops_queue', 'ops_deals', 'overview', 'deals', 'quotes', 'fulfil', 'inbox_api', 'archive', 'customers', 'pipeline', 'lang', 'outbox', 'web_app', 'roles']
runtime = ROOT / 'private_out/r11/test-runtime'
runtime.mkdir(exist_ok=True)
(runtime / 'sitecustomize.py').write_text("import console\nconsole.__path__.append('/opt/imgw/wechat/console')\n")
results = []
for suite in SUITES:
    if not (ROOT / 'tests' / suite).is_dir():
        continue
    code = "import console; console.__path__.append('/opt/imgw/wechat/console'); import pytest; raise SystemExit(pytest.main(['-q','-p','r11_guard','tests/" + suite + "']))"
    env = dict(os.environ, PYTHONPATH=str(runtime) + ':' + str(ROOT/'src') + ':' + str(ROOT/'tests'), PYTHONDONTWRITEBYTECODE='1')
    with (OUT / ('pytest-' + suite + '.log')).open('w') as log:
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    tail = (OUT / ('pytest-' + suite + '.log')).read_text().splitlines()[-3:]
    results.append(dict(suite=suite, exit_code=result.returncode, tail=tail))
    print(suite, result.returncode, ' | '.join(tail), flush=True)
(OUT / 'pytest-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
sys.exit(int(any(r['exit_code'] for r in results)))
