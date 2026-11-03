"""微信媒体脱敏盘点；只查 /tmp 私有 SQLite 副本，可选核验已有文件。"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat


SCANNER = Path(__file__).resolve().parents[1] / 'deploy/wechat-media/private_media_register.py'
STATES = frozenset(('queued', 'submitting', 'downloading', 'downloaded', 'archive_verified',
    'indexed', 'blocked', 'download_unknown', 'download_failed', 'complete',
    'RAW_ARCHIVED_NO_TEXT', 'interrupted', 'retry_wait', 'processing', 'source_changed'))
STATUSES = frozenset(('', 'ready', 'pending', 'processing', 'empty', 'archive_unavailable',
    'attachment_invalid', 'download_failed', 'extract_failed', 'failed_interrupted',
    'failed_invalid', 'unsupported_attachment', 'unsupported_format', 'download_invalid_url',
    'download_rejected', 'waiting_cloud_configuration', 'waiting_cloud_budget',
    'waiting_cloud_quota', 'cloud_response_rejected', 'cloud_outcome_unknown',
    'source_revoked', 'source_changed', 'original_saved_unparsed'))
ERRORS = frozenset(('', 'source_or_receipt_invalid', 'download_outcome_unknown',
    'interrupted_submission', 'download_paused_or_failed', 'status_temporarily_unavailable',
    'assistant_person_unauthorized', 'card_source_missing', 'card_authority_changed',
    'card_source_changed', 'download_identity_mismatch', 'download_status_invalid',
    'download_files_ambiguous', 'download_file_mismatch', 'nas_source_ambiguous',
    'catalog_schema_version_unsupported', 'video_annotation_unprivileged_required',
    'video_annotation_catalog_not_private', 'video_annotation_catalog_changed',
    'source_validation_failed', 'download_receipt_invalid', 'nas_receipt_invalid',
    'catalog_validation_failed'))
KINDS = frozenset(('image', 'file', 'video', 'voice', 'link', 'location', 'record', 'unknown'))
FORMATS = frozenset(('unknown', 'jpeg', 'png', 'webp', 'pdf', 'doc', 'docx', 'xlsx', 'xls',
    'pptx', 'html', 'xml', 'd4', 'r20', 'txt', 'csv', 'json', 'md'))
FIELDS = {
    'video_card_jobs': {'state', 'error_code', 'task_id', 'source_sha256', 'source_bytes',
                        'filename', 'receipt_filename', 'created_at'},
    'company_note_assets': {'status', 'kind', 'format', 'archive_status', 'archive_bytes'},
    'receive_collection_jobs': {'state', 'result_status'},
    'receive_collection_results': {'job_id'},
    'vk_nas_sources': {'filename', 'source_bytes', 'observed_at', 'source_sha256'},
}


def fixed(value, allowed):
    return value if value in allowed else '其他'


def private_directory(path, *, snapshot=False):
    path = Path(path).absolute()
    if ('..' in path.parts or any(p.is_symlink() for p in (path, *path.parents))
            or snapshot and not path.is_relative_to('/tmp')):
        raise ValueError('仅接受无上级路径和符号链接的目录；副本必须位于 /tmp')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('目录须由当前用户持有，权限设为 0700')
    return path


def read_copy(root, name, fields):
    path = root / name
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ValueError('副本须为当前用户持有的 0600 普通文件，不能使用软链接或硬链接')
    # Reject sidecars: the input must be a completed SQLite backup, not a copied
    # live main file whose committed pages still reside in another WAL.
    if any((root / (name + suffix)).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('副本含事务旁文件，请重新用 SQLite backup 生成完整副本')
    # A completed backup has every committed page in its main file. Immutable
    # avoids SQLite creating even an empty WAL/SHM pair for a WAL-mode backup.
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        for table, required in fields.items():
            if not required <= {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}:
                raise ValueError('副本表结构不匹配，请先核对生产源码与版本')
    except Exception:
        db.close()
        raise
    return db


def inventory(snapshot):
    root = private_directory(snapshot, snapshot=True)
    with closing(read_copy(root, 'state.sqlite3', FIELDS)) as state, closing(read_copy(
            root, 'registry.sqlite3', {'assets': {'sha256', 'source_verified'},
            'observations': {'relative_path', 'sha256'}})) as registry:
        video = Counter()
        reasons = Counter()
        for status, code, count in state.execute(
                'SELECT state,COALESCE(error_code,\'\'),COUNT(*) FROM video_card_jobs GROUP BY state,error_code'):
            video[fixed(status, STATES)] += count
            reasons[(fixed(status, STATES), fixed(code, ERRORS))] += count
        notes = Counter()
        for status, kind, format, archive, count, low, high in state.execute('''
                SELECT status,kind,format,archive_status,COUNT(*),MIN(archive_bytes),MAX(archive_bytes)
                FROM company_note_assets GROUP BY status,kind,format,archive_status'''):
            # Ranges are aggregated metadata; no labels, IDs, bodies or paths.
            key = (fixed(status, STATUSES), fixed(kind, KINDS), fixed(format, FORMATS),
                   fixed(archive, STATUSES), low, high)
            notes[key] += count
        receive = Counter()
        for status, result, count in state.execute('''SELECT state,result_status,COUNT(*)
                FROM receive_collection_jobs GROUP BY state,result_status'''):
            receive[(fixed(status, STATES), fixed(result, STATUSES))] += count
        result = {
            '口径': '只读副本统计；作业结束、原文件归档、正文就绪、知识索引分别计算，不能互相代替',
            '视频号': {'总数': sum(video.values()), '状态': dict(sorted(video.items())),
                '状态与原因': [{'状态': a, '原因': b, '数量': n} for (a, b), n in sorted(reasons.items())]},
            '公司资料附件': {'总数': sum(notes.values()), '分组': [dict(zip(
                ('状态', '种类', '格式', '归档状态', '最小字节', '最大字节', '数量'), (*k, n)))
                for k, n in sorted(notes.items())]},
            '接收采集': {'总数': sum(receive.values()), '分组': [
                {'状态': a, '结果': b, '数量': n} for (a, b), n in sorted(receive.items())],
                '持久正文结果': state.execute('SELECT COUNT(*) FROM receive_collection_results').fetchone()[0]},
            '私有媒体登记': {
                '内容哈希数': registry.execute('SELECT COUNT(*) FROM assets').fetchone()[0],
                '路径观察数': registry.execute('SELECT COUNT(*) FROM observations').fetchone()[0],
                '独立相对路径数': registry.execute('SELECT COUNT(DISTINCT relative_path) FROM observations').fetchone()[0],
                '已标记来源验证数': registry.execute('SELECT COUNT(*) FROM assets WHERE source_verified<>0').fetchone()[0]},
        }
        if (root / 'catalog.sqlite3').exists():
            with closing(read_copy(root, 'catalog.sqlite3', {
                'multimedia_assets': {'asset_id', 'owner', 'source_scope', 'source_sha256'},
                'knowledge_chunks': {'asset_id', 'owner', 'source_scope'}})) as catalog:
                matched = Counter()
                for status, digest, size, filename, receipt, created in state.execute('''SELECT state,
                        source_sha256,source_bytes,filename,receipt_filename,created_at FROM video_card_jobs'''):
                    if not digest:
                        continue
                    nas = {row[0] for row in state.execute('''SELECT DISTINCT source_sha256 FROM vk_nas_sources
                        WHERE filename=? AND source_bytes=? AND observed_at>=?''',
                        (receipt or filename, size, created))}
                    chunks = catalog.execute('''SELECT EXISTS(SELECT 1 FROM multimedia_assets a
                        JOIN knowledge_chunks k ON k.asset_id=a.asset_id AND k.owner=a.owner
                        AND k.source_scope=a.source_scope WHERE a.source_sha256=?)''', (digest,)).fetchone()[0]
                    matched[(fixed(status, STATES), nas == {digest}, bool(chunks))] += 1
                result['视频号对账'] = [dict(zip(('状态', '唯一NAS关联', '已有同范围知识块', '数量'), (*k, n)))
                    for k, n in sorted(matched.items())]
                result['对账边界'] = '哈希和范围关联只证明已有内容，未读取当前授权配置，不能据此解冻、改状态或再次下载'
    return result


def source_probe(snapshot, source_root, blob_root):
    """Read complete existing bytes and discard them; never call scanner.run()."""
    snapshot = private_directory(snapshot, snapshot=True)
    blob_root = private_directory(blob_root)
    spec = importlib.util.spec_from_file_location('r7b_scanner', SCANNER)
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    source_root = scanner.checked_root(source_root)
    if '..' in source_root.parts:
        raise ValueError('源目录不能包含上级路径')
    identity = scanner.mount_for_path(source_root)
    original = source_root.lstat()

    def guard(path):
        current = source_root.lstat()
        child = path.lstat()
        if (scanner.mount_for_path(path) != identity or child.st_dev != original.st_dev
                or (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)):
            raise scanner.ScanInterrupted('source_mount_changed')

    with closing(read_copy(snapshot, 'registry.sqlite3', {'assets': {'sha256'}})) as registry:
        known = {r[0] for r in registry.execute('SELECT sha256 FROM assets')}
    preflight = {'rejected': 0, 'errors': {}}
    candidates, total = scanner.enumerate_candidates(source_root, guard, preflight)
    counts, errors = Counter(), Counter(preflight['errors'])
    for rel, size in candidates:
        fd = parent = None
        try:
            guard(source_root / rel)
            fd, parent = scanner.io_call('source_open', scanner.nofollow_source, source_root, rel)
            before = scanner.io_call('source_stat', os.fstat, fd)
            digest, read = hashlib.sha256(), 0
            while data := scanner.io_call('source_read', os.read, fd, 1024 * 1024):
                digest.update(data)
                read += len(data)
                if read > scanner.MAX_BYTES:
                    raise ValueError('source_grew')
            after = scanner.io_call('source_stat', os.fstat, fd)
            named = scanner.io_call('source_stat', os.stat, rel.name, dir_fd=parent, follow_symlinks=False)
            if (read != size or scanner.signature(before) != scanner.signature(after)
                    or scanner.signature(before) != scanner.signature(named)):
                raise ValueError('source_changed')
            guard(source_root / rel)
            counts['完整读取且未变化'] += 1
            value = digest.hexdigest()
            counts['哈希已登记' if value in known else '哈希未登记'] += 1
            got, length, info = scanner.io_call('output_verify', scanner.digest_file,
                                               blob_root / (value + '.blob'))
            if got != value or length != read or info.st_nlink != 1:
                raise ValueError('blob_conflict')
            counts['本地归档字节一致'] += 1
        except OSError as error:
            errors[scanner.io_code(error)] += 1
        except ValueError:
            errors['文件校验失败'] += 1
        finally:
            if fd is not None:
                os.close(fd)
            if parent is not None:
                os.close(parent)
    return {'候选文件': len(candidates), '候选字节': total,
            '已在预检拒绝': preflight['rejected'], '核验': dict(counts), '错误': dict(errors),
            '边界': '当前进程与当前挂载的只读核验；不等于历史服务命名空间或当前业务授权验证'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读核对微信媒体私有副本，输出脱敏统计', add_help=False)
    parser.add_argument('-h', '--help', action='help', help='显示用法')
    parser.add_argument('--snapshot', required=True, type=Path, help='/tmp 内的 0700 副本目录')
    parser.add_argument('--source-root', type=Path, help='可选：完整读取已有媒体的可信根目录')
    parser.add_argument('--blob-root', type=Path, help='可选：与源目录一起提供的既有私有归档目录')
    args = parser.parse_args(argv)
    if bool(args.source_root) != bool(args.blob_root):
        parser.error('源目录和归档目录需要一起提供；不提供时只查询副本')
    try:
        result = inventory(args.snapshot)
        if args.source_root:
            result['现存文件只读核验'] = source_probe(args.snapshot, args.source_root, args.blob_root)
    except Exception:
        print('无法完成只读诊断，请检查副本权限、表结构和挂载；未修改业务状态')
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
