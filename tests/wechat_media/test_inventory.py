"""脱敏统计不把 complete 当正文成功，也不以已有知识块代替授权。"""
from contextlib import closing
import hashlib
import json
import os
import sqlite3

import pytest


def fingerprint(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}


def test_counts_are_distinct_and_read_only(snapshot, diagnostic):
    before = fingerprint(snapshot)
    result = diagnostic.inventory(snapshot)
    assert result['视频号']['状态'] == {'blocked': 1, 'download_unknown': 1}
    assert result['接收采集']['总数'] == 3
    assert result['接收采集']['持久正文结果'] == 1
    assert result['视频号对账'] == [{'状态': 'blocked', '唯一NAS关联': True, '已有同范围知识块': True, '数量': 1}]
    assert result['私有媒体登记']['已标记来源验证数'] == 0
    encoded = json.dumps(result, ensure_ascii=False)
    for hidden in ('虚构隐藏', '虚构持有人', '虚构范围', '虚构.mp4', 'a' * 64):
        assert hidden not in encoded
    assert fingerprint(snapshot) == before


def test_untrusted_codes_never_leave_report(snapshot, diagnostic):
    with closing(sqlite3.connect(snapshot / 'state.sqlite3')) as db, db:
        db.execute('UPDATE video_card_jobs SET error_code=?', ('虚构隐藏凭据与路径',))
        db.execute('UPDATE company_note_assets SET kind=?,format=?,status=?', ('虚构隐藏种类', '虚构隐藏格式', '虚构隐藏状态'))
    report = json.dumps(diagnostic.inventory(snapshot), ensure_ascii=False)
    assert '虚构隐藏' not in report
    assert '其他' in report


def test_unique_nas_entry_still_requires_exact_hash(snapshot, diagnostic):
    with closing(sqlite3.connect(snapshot / 'state.sqlite3')) as db, db:
        db.execute('UPDATE vk_nas_sources SET source_sha256=?', ('b' * 64,))
    assert diagnostic.inventory(snapshot)['视频号对账'][0]['唯一NAS关联'] is False


def test_chunks_in_another_scope_do_not_count(snapshot, diagnostic):
    with closing(sqlite3.connect(snapshot / 'catalog.sqlite3')) as db, db:
        db.execute('UPDATE knowledge_chunks SET source_scope=?', ('虚构另一范围',))
    assert diagnostic.inventory(snapshot)['视频号对账'][0]['已有同范围知识块'] is False


def test_completed_wal_backup_does_not_create_sidecars(snapshot, diagnostic):
    with closing(sqlite3.connect(snapshot / 'state.sqlite3')) as db, db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    before = fingerprint(snapshot)
    diagnostic.inventory(snapshot)
    assert fingerprint(snapshot) == before


@pytest.mark.parametrize('mode', [0o755, 0o777])
def test_public_snapshot_refused(snapshot, diagnostic, mode):
    snapshot.chmod(mode)
    with pytest.raises(ValueError, match='0700'):
        diagnostic.inventory(snapshot)


def test_snapshot_parent_traversal_refused(snapshot, diagnostic):
    with pytest.raises(ValueError, match='上级路径'):
        diagnostic.inventory(snapshot / '..' / snapshot.name)


def test_symlink_directory_refused(snapshot, diagnostic):
    link = snapshot.parent / 'linked'
    link.symlink_to(snapshot, target_is_directory=True)
    with pytest.raises(ValueError, match='符号链接'):
        diagnostic.inventory(link)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'public_file', 'wal'])
def test_unsafe_snapshot_file_refused(snapshot, diagnostic, kind):
    path = snapshot / 'state.sqlite3'
    if kind == 'symlink':
        original = snapshot.parent / 'original.sqlite3'
        path.rename(original)
        path.symlink_to(original)
    elif kind == 'hardlink':
        os.link(path, snapshot.parent / 'alias.sqlite3')
    elif kind == 'public_file':
        path.chmod(0o644)
    else:
        (snapshot / 'state.sqlite3-wal').touch(mode=0o600)
    with pytest.raises(ValueError, match='副本'):
        diagnostic.inventory(snapshot)


def test_schema_mismatch_is_explicit(snapshot, diagnostic):
    with closing(sqlite3.connect(snapshot / 'state.sqlite3')) as db, db:
        db.execute('DROP TABLE company_note_assets')
    with pytest.raises(ValueError, match='表结构'):
        diagnostic.inventory(snapshot)


def test_cli_has_fixed_error_without_path(snapshot, diagnostic, capsys):
    snapshot.chmod(0o755)
    assert diagnostic.main(['--snapshot', str(snapshot)]) == 2
    output = capsys.readouterr().out
    assert '请检查副本权限' in output
    assert str(snapshot) not in output


def test_probe_reads_only_and_detects_mismatched_archive(tmp_path, snapshot, diagnostic):
    source, blobs = tmp_path / 'source', tmp_path / 'blobs'
    source.mkdir(mode=0o700)
    blobs.mkdir(mode=0o700)
    data = b'fictional opaque media bytes'
    (source / '虚构隐藏附件.jpg').write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    blob = blobs / (digest + '.blob')
    blob.write_bytes(data)
    blob.chmod(0o600)
    before = fingerprint(source), fingerprint(blobs)
    result = diagnostic.source_probe(snapshot, source, blobs)
    assert result['核验']['完整读取且未变化'] == 1
    assert result['核验']['本地归档字节一致'] == 1
    assert not result['错误']
    assert (fingerprint(source), fingerprint(blobs)) == before
    assert '虚构隐藏' not in json.dumps(result, ensure_ascii=False)
    blob.write_bytes(b'fictional mismatch')
    assert diagnostic.source_probe(snapshot, source, blobs)['错误'] == {'文件校验失败': 1}
