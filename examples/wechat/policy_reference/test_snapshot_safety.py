import os,pathlib,re,subprocess,sys,unittest

ROOT=pathlib.Path(__file__).parent
class SafetyTests(unittest.TestCase):
    def test_entry_requires_explicit_audit_mode(self):
        for name in ('company_knowledge.py','collection_policy.py'):
            r=subprocess.run([sys.executable,'-I','-B',str(ROOT/name)],capture_output=True,text=True,env={'PATH':os.environ.get('PATH','')},timeout=5)
            self.assertNotEqual(r.returncode,0);self.assertIn('Reference snapshot is for audit only.',r.stderr)
    def test_no_deployment_paths_or_private_network_literals(self):
        for name in ('company_knowledge.py','collection_policy.py'):
            text=(ROOT/name).read_text();compile(text,name,'exec')
            self.assertNotRegex(text,r'/(?:opt|etc|var|home|mnt|Users)/')
            self.assertNotRegex(text,r'\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b')
if __name__=='__main__':unittest.main()
