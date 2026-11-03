from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from console.kb import Hub
from kb_fixtures import record, seed

spec = importlib.util.spec_from_file_location('kb_cli', Path(__file__).resolve().parents[2]/'scripts/kb_search.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class CliTests(unittest.TestCase):
    def test_checkout_package_wins_over_an_installed_console(self):
        with tempfile.TemporaryDirectory() as root:
            package = Path(root)/'console'
            package.mkdir()
            (package/'__init__.py').write_text("raise RuntimeError('fictional installed package must not shadow checkout')")
            code = ('import runpy; runpy.run_path(' + repr(str(Path(cli.__file__).resolve())) +
                    "); from console.kb import Hub; print('checkout-import-ok')")
            result = subprocess.run([sys.executable,'-c',code],env={**os.environ,'PYTHONPATH':root},
                                    capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'checkout-import-ok')

    def test_json_and_human_readonly_company_context_missing_numpy(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            hub = Hub(root/'catalog.sqlite3', root/'brief.md', root/'history.sqlite3',embedder=None)
            seed(hub.index.path)
            original = hub.index.path.read_bytes()
            for options in ([], ['--json']):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err), patch('console.kb.index.numpy_module',return_value=None):
                    self.assertEqual(cli.main(['外箱尺寸',*options],hub=hub),0)
                self.assertIn('numpy',err.getvalue())
                if options:
                    result = json.loads(out.getvalue())
                    self.assertEqual(result['mode'],'lexical')
                    self.assertIn('company_brief',result)
                else:
                    self.assertIn('公司背景',out.getvalue())
                    self.assertIn('2026-',out.getvalue())
                    self.assertIn('供应商资料',out.getvalue())
            self.assertEqual(hub.index.path.read_bytes(),original)
            self.assertFalse(hub.brief.path.exists())
            self.assertFalse(hub.brief.history_path.exists())

    def test_invalid_and_missing_no_exception_path_leak(self):
        with tempfile.TemporaryDirectory() as root:
            hub = Hub(Path(root)/'missing.sqlite3',Path(root)/'brief.md',Path(root)/'history.sqlite3',embedder=None)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(cli.main(['灯','--top','21'],hub=hub),2)
                self.assertEqual(cli.main(['灯'],hub=hub),1)
            self.assertNotIn(root,err.getvalue())
