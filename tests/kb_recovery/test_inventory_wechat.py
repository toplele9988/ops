"""虚构来源盘点：历史完成不受新拼窗影响；未知证据优先隔离。"""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wechat_inventory_test', ROOT / 'deploy/kb/inventory_wechat.py')
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def synthetic_snapshot(tmp_path):
    snapshot = tmp_path / 'private'
    snapshot.mkdir(mode=0o700)
    ns = inventory.algorithms()
    room = 'fictional-wechat-room'
    room_sha = inventory.sha(room)
    sources = [dict(evidence_id='fictional-source-' + str(number), classification_id='fictional-class',
                    callback_sha256='fictional-callback-' + str(number), source_created_at=at,
                    received_at=at, created_at=at, body_text='虚构参数' + str(number),
                    owner='fictional-owner', source_scope='fictional-scope', price_kind='not_price',
                    is_cost_price=0, source_kind='message', chatroom_id=room_sha)
               for number, at in ((1, 100), (2, 200), (3, 300))]
    ns['kb_authorized'] = lambda db, ctx: sources[:1]
    completed = ns['units'](None, {}, [room_sha])[0]
    ns['kb_authorized'] = lambda db, ctx: sources[:2]
    unknown = ns['units'](None, {}, [room_sha])[0]
    for name in ('catalog', 'calls', 'state'):
        path = snapshot / (name + '.sqlite3')
        path.touch(mode=0o600)
    with sqlite3.connect(snapshot / 'catalog.sqlite3') as db:
        db.executescript('''
            CREATE TABLE channel_business_evidence(evidence_id,classification_id,callback_sha256,
                source_created_at,received_at,created_at,body_text,owner,source_scope,price_kind,
                is_cost_price,source_kind);
            CREATE TABLE channel_role_registry(classification_id,owner,source_scope,channel_kind,
                channel_role,channel_id_sha256,account_sha256);
            CREATE TABLE kb_segment(unit_id,unit_sha256,status,created_at,chatroom_id);
            CREATE TABLE kb_learning_segment(unit_id,unit_sha256,status,created_at,chatroom_id);
            CREATE TABLE kb_segment_audit(original_table,original_row_json);
            CREATE TABLE kb_fact(fact_id);
            CREATE TABLE kb_learning_fact(fact_id);
        ''')
        db.execute('INSERT INTO channel_role_registry VALUES(?,?,?,?,?,?,?)',
                   ('fictional-class','fictional-owner','fictional-scope','group',
                    'CHANNEL_ROLE_SUPPLIER',room_sha,'fictional-account'))
        for source in sources:
            db.execute('INSERT INTO channel_business_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       tuple(source[field] for field in ('evidence_id','classification_id','callback_sha256',
                             'source_created_at','received_at','created_at','body_text','owner','source_scope',
                             'price_kind','is_cost_price','source_kind')))
        db.execute('INSERT INTO kb_segment VALUES(?,?,?,?,?)',
                   (completed['unit_id'],completed['unit_sha256'],'no_facts',150,room))
    with sqlite3.connect(snapshot / 'calls.sqlite3') as db:
        db.execute('CREATE TABLE calls(cache_key,attempt,prompt_sha256,model,state,response_json,response_sha256,created_at,updated_at)')
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',
                   ('fictional-key',1,inventory.sha(ns['PROMPT']+unknown['text']),
                    'fictional-model','hold',None,None,250,251))
    with sqlite3.connect(snapshot / 'state.sqlite3') as db:
        db.execute('CREATE TABLE channel_role_intake(observation_json)')
        db.execute('CREATE TABLE received_callback_archive(account_sha256,source_scope,callback_sha256,source_created_at,received_at,message_type)')
    return snapshot


def test_inventory_retains_historical_completion_without_using_new_window(tmp_path):
    path = synthetic_snapshot(tmp_path)
    with sqlite3.connect(path / 'calls.sqlite3') as db:
        # This unrelated unknown is not source-linked and must stay visibly unresolved.
        db.execute('UPDATE calls SET prompt_sha256=?', ('f' * 64,))
    result = inventory.inventory(path)
    assert result['分类']['已完成']['证据数'] == 1
    assert result['分类']['从未请求']['证据数'] == 1
    assert result['分类']['请求覆盖待确认']['证据数'] == 1
    assert result['未能精确溯源请求数'] == 1
    assert result['未知请求数'] == 0
    assert 'fictional-source' not in str(result)


def test_inventory_unknown_window_overrides_prior_completion(tmp_path):
    result = inventory.inventory(synthetic_snapshot(tmp_path))
    assert result['分类']['结果未知']['证据数'] == 2
    assert result['分类']['从未请求']['证据数'] == 1
    assert result['分类']['已完成']['证据数'] == 0
    assert result['精确溯源请求数'] == 1
    assert result['未知请求数'] == 1


@pytest.mark.parametrize('mode', [0o755, 0o777])
def test_inventory_rejects_nonprivate_directory(tmp_path, mode):
    snapshot = tmp_path / 'public'
    snapshot.mkdir(mode=mode)
    snapshot.chmod(mode)
    with pytest.raises(ValueError, match='0700'):
        inventory.private(snapshot)


def test_inventory_rejects_symlink_snapshot(tmp_path):
    snapshot = synthetic_snapshot(tmp_path)
    link = tmp_path / 'alias'
    link.symlink_to(snapshot, target_is_directory=True)
    with pytest.raises(ValueError, match='符号链接'):
        inventory.private(link)


def test_inventory_two_durable_readers_can_resume_local_steps(tmp_path):
    import json
    path = synthetic_snapshot(tmp_path)
    raw = json.dumps({'content': json.dumps({'facts': []})})
    with sqlite3.connect(path / 'calls.sqlite3') as db:
        prompt = db.execute('SELECT prompt_sha256 FROM calls').fetchone()[0]
        db.execute('UPDATE calls SET state=?,model=?,response_json=?,response_sha256=?',
                   ('complete','fictional-reader-a',raw,inventory.sha(raw)))
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',
                   ('fictional-key-b',1,prompt,'fictional-reader-b','complete',raw,inventory.sha(raw),250,251))
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',
                   ('fictional-unlinked-hold',1,'f'*64,'fictional-held','hold',None,None,400,401))
    result = inventory.inventory(path)
    assert result['分类']['已完成']['证据数'] == 1
    assert result['分类']['有可靠响应但本地未完成']['证据数'] == 1
    assert result['双响应仅续本地候选单元数'] == 1
    assert result['单响应尚缺第二读者单元数'] == 0


def test_inventory_three_held_document_chunks_quarantine_whole_source(tmp_path):
    path = synthetic_snapshot(tmp_path)
    body = '\n'.join('虚构资料参数' + str(number) + 'abcdefghij'*6 for number in range(100))
    with sqlite3.connect(path / 'catalog.sqlite3') as db:
        db.row_factory = sqlite3.Row
        db.execute('UPDATE channel_business_evidence SET body_text=? WHERE evidence_id=?',
                   (body,'fictional-source-2'))
        source = dict(db.execute('SELECT * FROM channel_business_evidence WHERE evidence_id=?',
                                 ('fictional-source-2',)).fetchone(),
                      chatroom_id=inventory.sha('fictional-wechat-room'))
    ns = inventory.algorithms()
    chunks = ns['_document_units'](source)
    assert len(chunks) >= 3
    with sqlite3.connect(path / 'calls.sqlite3') as db:
        db.execute('DELETE FROM calls')
        for number, unit in enumerate(chunks[:3]):
            db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',
                       ('fictional-chunk-'+str(number),1,inventory.sha(ns['PROMPT']+unit['text']),
                        'fictional-held-'+str(number),'hold',None,None,250,251))
    result = inventory.inventory(path)
    assert result['未知请求数'] == 3
    assert result['未知源证据数'] == 1
    assert result['分类']['结果未知']['证据数'] == 1
    assert result['分类']['已完成']['证据数'] == 1


def test_inventory_rejects_parent_path_escape():
    with pytest.raises(ValueError, match='/tmp'):
        inventory.private(Path('/tmp/../var/lib'))


def test_local_resume_rejects_source_held_in_another_prompt(tmp_path):
    import json
    path = synthetic_snapshot(tmp_path)
    ns = inventory.algorithms()
    row = {'evidence_id':'fictional-source-1', 'body':'虚构参数1', 'at':100}
    unit = ns['_make_unit']('KC:fictional-source-1:1', inventory.sha('fictional-wechat-room'), [row])
    prompt_sha = inventory.sha(ns['PROMPT'] + unit['text'])
    raw = json.dumps({'content': json.dumps({'facts': []})})
    with sqlite3.connect(path / 'calls.sqlite3') as db:
        for reader in ('a','b'):
            db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',
                       ('fictional-previous-'+reader,1,prompt_sha,'fictional-reader-'+reader,
                        'complete',raw,inventory.sha(raw),160,161))
    result = inventory.inventory(path)
    assert result['分类']['结果未知']['证据数'] == 2
    assert result['双响应仅续本地候选单元数'] == 0


def test_inventory_new_source_alias_of_unknown_is_excluded_from_execution_count(tmp_path):
    path = synthetic_snapshot(tmp_path)
    with sqlite3.connect(path / 'catalog.sqlite3') as db:
        db.execute('UPDATE channel_business_evidence SET body_text=? WHERE evidence_id=?',
                   ('虚构参数1','fictional-source-3'))
    result = inventory.inventory(path)
    assert result['分类']['从未请求']['证据数'] == 1
    assert result['从未请求候选命中未知原始来源或正文数'] == 1
    assert result['从未请求排除未知交叉后证据数'] == 0
    assert result['从未请求排除未知交叉后单元数'] == 0
