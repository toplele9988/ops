import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).with_name('reference_snapshot')


class SnapshotTests(unittest.TestCase):
    def test_syntax_and_disabled_entrypoint(self):
        for filename in ('icbu_inbound_receiver.py','icbu_live_drafts.py'):
            with self.subTest(filename=filename):
                path=ROOT/filename;source=path.read_text();compile(source,filename,'exec')
                result=subprocess.run([sys.executable,str(path)],capture_output=True,text=True,timeout=5)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('Reference snapshot cannot be run',result.stderr)
    def test_no_deployment_paths_or_private_lan_literals(self):
        for path in ROOT.glob('*.py'):
            text=path.read_text()
            self.assertNotRegex(text,r'/(?:opt|etc|var|home)/')
            self.assertNotRegex(text,r'\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.(?:\d{1,3}\.)\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})\b')
    def test_full_function_and_class_structure_is_present(self):
        for filename,count in (('icbu_inbound_receiver.py',49),('icbu_live_drafts.py',28)):
            tree=ast.parse((ROOT/filename).read_text())
            self.assertEqual(sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) for n in ast.walk(tree)),count)

if __name__=='__main__':unittest.main()
