"""Matching is parameterized and disabled until explicitly configured."""
import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from console.owner_inbox import _matching_live_drafts

class SyntheticDraftTests(unittest.TestCase):
    def test_explicit_account_and_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'inbound.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE inbound(id TEXT,state TEXT,created_at REAL,draft TEXT,payload TEXT)')
                db.execute('CREATE TABLE live_draft_jobs(inbound_id TEXT,state TEXT,synthetic INTEGER)')
                mid='12345678-1234-1234-1234-123456789abc'
                payload={'account':'fixture_account','conversation_id':'fixture_chat','history':False,'role':'buyer','live_provenance':{'schema':'fixture-protocol-v1'}}
                db.execute('INSERT INTO inbound VALUES(?,?,?,?,?)',(mid,'draft_ready',1,'fixture draft',json.dumps(payload)))
                db.execute('INSERT INTO live_draft_jobs VALUES(?,?,?)',(mid,'draft_ready',0))
            with patch.dict(os.environ,{},clear=True):
                self.assertEqual(_matching_live_drafts(path,'fixture_account','fixture_chat')['status'],'not_supported')
            with patch.dict(os.environ,{'LECHU_ICBU_DRAFT_ACCOUNT':'fixture_account','LECHU_ICBU_PROVENANCE_SCHEMA':'fixture-protocol-v1'},clear=True):
                self.assertEqual(_matching_live_drafts(path,'other_account','fixture_chat')['status'],'not_supported')
                result=_matching_live_drafts(path,'fixture_account','fixture_chat')
                self.assertEqual(result['status'],'ok')
                self.assertEqual(len(result['items']),1)
                self.assertEqual(result['items'][0]['draft'],'fixture draft')
                self.assertEqual(_matching_live_drafts(path,'fixture_account',"fixture_chat' OR 1=1 --")['items'],[])
