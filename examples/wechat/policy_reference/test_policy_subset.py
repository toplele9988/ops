"""AST-isolated tests for pure helpers only; never imports live business modules."""
import ast,hashlib,json,math,os,re,unittest
from pathlib import Path
from urllib.parse import urlsplit

root=Path(os.environ.get('FIXTURE_SOURCE_ROOT',Path(__file__).parent)).resolve()
def subset(filename,functions,assignments,classes=()):
    tree=ast.parse((root/filename).read_text());nodes=[]
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in set(functions)|set(classes):nodes.append(n)
        elif isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id in assignments for x in n.targets):nodes.append(n)
    code=ast.Module(body=nodes,type_ignores=[]);ast.fix_missing_locations(code)
    scope={'json':json,'re':re,'math':math,'hashlib':hashlib,'os':os,'urlsplit':urlsplit}
    exec(compile(code,filename,'exec'),scope);return scope

policy=subset('collection_policy.py',{'_need','_ident','_stamp','enabled','_public_shape','public_selection','allows_unclassified_media','group_view'},{'PUBLIC_FIELDS'},{'PublicReplyError'})
knowledge=subset('company_knowledge.py',{'_json','_clean','_stamp','_kind','_origin_key','source_id_for'},{'_SECRET','_PATH','_TRANSPORT'})

def fixture_config():
    gid='group-fixture@chatroom';app='app-fixture';owner='owner-fixture';profile='profile-fixture';scope='scope-fixture'
    group={'enabled':True,'reply_mode':'participate','business_role':'learning','group_name':'示例业务群','own_wxid':owner,'memory_scope_id':scope,'daily_limit':5,'group_cooldown_seconds':600,'sender_cooldown_seconds':600,'ai':{'enabled':True,'mode':'support','participation_mode':'community','digest_enabled':True,'digest_daily_limit':24,'digest_interval_seconds':1800}}
    return {'collection_only_groups':True,'base_url':'https://api.fixture.invalid','app_id':app,'assistant_profile_id':profile,'owner_control':{'own_wxid':owner},'groups':{gid:group},'community_public_reply':{'enabled':True,'active_after':123,'app_id':app,'assistant_profile_id':profile,'group_id':gid,'own_wxid':owner,'memory_scope_id':scope}}

class PolicyTests(unittest.TestCase):
    def test_identity_validation(self):
        self.assertTrue(policy['_ident']('fixture-id'));self.assertFalse(policy['_ident']('bad value'));self.assertFalse(policy['_ident'](7))
    def test_timestamp_rejects_boolean_nonfinite_and_zero(self):
        for value in (True,float('nan'),float('inf'),0,-1):self.assertFalse(policy['_stamp'](value))
        self.assertTrue(policy['_stamp'](123))
    def test_collection_enabled_only_on_explicit_true(self):
        self.assertTrue(policy['enabled']({'collection_only_groups':True}));self.assertFalse(policy['enabled']({'collection_only_groups':'true'}));self.assertFalse(policy['enabled']({}))
    def test_unclassified_collection_gate(self):
        cfg={'collection_only_groups':True,'group_onboarding':{'auto_collect':True}}
        self.assertTrue(policy['allows_unclassified_media'](cfg,{'business_role':'unclassified','auto_collection_source':'standing_group_collection'}))
        self.assertFalse(policy['allows_unclassified_media'](cfg,{'business_role':'other','auto_collection_source':'standing_group_collection'}))
    def test_group_view_keeps_collection_silent(self):
        cfg={'collection_only_groups':True,'groups':{'group-fixture@chatroom':{'enabled':True,'reply_mode':'participate'}}}
        self.assertEqual(policy['group_view'](cfg,'group-fixture@chatroom')['reply_mode'],'silent');self.assertIsNone(policy['group_view'](cfg,'missing'))
    def test_malformed_public_selection_rejected(self):
        with self.assertRaises(policy['PublicReplyError']):policy['_public_shape']({'community_public_reply':{'enabled':True}})
    def test_synthetic_public_selection_for_snapshot_only(self):
        if os.environ.get('FIXTURE_IS_SNAPSHOT')!='1':self.skipTest('original has intentional private identity and host binding')
        cfg=fixture_config();selected=policy['public_selection'](cfg);self.assertIsNotNone(selected)
        cfg['groups'][selected['group_id']]['own_wxid']='other-fixture';self.assertIsNone(policy['public_selection'](cfg))
    def test_kind_categories(self):
        for text,role,want in (('please quote','buyer','quote'),('shipping','buyer','logistics'),('we need','buyer','customer_requirement'),('lighting','buyer','product'),('misc','supplier','supplier')):
            self.assertEqual(knowledge['_kind'](text,role),want)
    def test_clean_removes_fixture_identifiers(self):
        value='synthetic '+'to'+'ken=fixturevalue https://source.fixture.invalid/path owner-fixture'
        cleaned=knowledge['_clean'](value,blocked=['owner-fixture']);self.assertNotIn('fixturevalue',cleaned);self.assertNotIn('source.fixture.invalid',cleaned);self.assertNotIn('owner-fixture',cleaned)
    def test_source_id_scope_and_idempotency(self):
        settings={'company_id':'fixture-company'};origin={'app_id':'a','message_id':'m','channel':'c','id':'i'}
        first=knowledge['source_id_for'](settings,'wechat','fixture',origin)
        self.assertEqual(first,knowledge['source_id_for'](settings,'wechat','fixture',origin));self.assertNotEqual(first,knowledge['source_id_for'](settings,'whatsapp','fixture',origin))

if __name__=='__main__':unittest.main()
