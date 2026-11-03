"""首次安装前逐单元复核原动态授权；只生成 /tmp 私有审核稿，不请求模型。"""
from __future__ import annotations

from collections import Counter
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import backlog as b
import prepare_install as p
import provenance
import inventory_wechat as wechat
import inventory_alibaba as alibaba
import source_gate
import plan_review
from production_adapter import (SOURCE_FIELDS, AlibabaAdapter, single_alibaba_unit,
                                wechat_entry, wechat_prompt)
from authorization_runtime import original_authorization


class AuthorizationError(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise AuthorizationError(code)


def read_only(path):
    # snapshot 已由停写窗口备份为无 WAL 文件；immutable 避免创建 SHM。
    db = sqlite3.connect(Path(path).as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


@contextmanager
def quiet_readonly(snapshot_root):
    """进程内拒绝写入、网络和子进程；被原函数吞掉的拒绝也必须全停。"""
    state = {'active': True, 'violation': False}

    class Sink:
        def write(self, value):
            return len(value)

        def flush(self):
            pass

    def audit(event, args):
        if not state['active']:
            return
        blocked = event.startswith(('socket.', 'subprocess.', 'os.spawn', 'os.exec'))
        blocked |= event in {'os.system', 'os.fork', 'os.forkpty', 'os.remove', 'os.rename',
                             'os.rmdir', 'os.mkdir', 'os.chmod', 'os.chown', 'os.truncate',
                             'os.link', 'os.symlink', 'os.utime'}
        if event == 'open':
            _, mode, flags = args
            blocked |= bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        if event == 'sqlite3.connect':
            target = str(args[0])
            parsed = urlsplit(target)
            path = Path(unquote(parsed.path))
            query = parse_qs(parsed.query)
            snapshot = (path.is_relative_to(snapshot_root) and path.suffix == '.sqlite3'
                        and query.get('mode') == ['ro'] and set(query) <= {'mode', 'immutable'})
            # 原 owner_pairs 可选授权仅查 binding；runtime 会先核对无 WAL 帧，
            # 再把原只读连接改为 immutable，禁止写生产 SHM 或扫描消息正文。
            binding = (path == Path('/var/lib/imgw/whatsapp/messages.sqlite3')
                       and query == {'mode': ['ro'], 'immutable': ['1']})
            blocked |= not (parsed.scheme == 'file' and not parsed.netloc and not parsed.fragment
                            and '..' not in path.parts and (snapshot or binding))
        if blocked:
            state['violation'] = True
            raise AuthorizationError('AUTH_SIDE_EFFECT')

    # 审计钩子不能卸载，退出后关闭该钩子；CLI 只有一个审阅进程。
    saved_bytecode, saved_logging = sys.dont_write_bytecode, logging.root.manager.disable
    sys.dont_write_bytecode = True
    logging.disable(sys.maxsize)
    sys.addaudithook(audit)
    try:
        with redirect_stdout(Sink()), redirect_stderr(Sink()):
            yield
        need(not state['violation'], 'AUTH_SIDE_EFFECT')
    finally:
        state['active'] = False
        sys.dont_write_bytecode = saved_bytecode
        logging.disable(saved_logging)


def snapshot_pins(session):
    session.marker('snapshot')
    session.marker('inventory')
    root = session.root / 'snapshot'
    manifest = p.read_json(root / 'snapshots.json')
    expected = {channel + '/' + name for channel, names in p.SOURCES.items() for name in names}
    need(manifest['schema'] == 'kb-install-snapshots-v1'
         and set(manifest['files']) == expected, 'AUTH_SNAPSHOT_SET')
    pins = {}
    for name, row in manifest['files'].items():
        path = root / name
        need(b.is_sha(row['sha256']) and p.sha(p.raw_file(path)) == row['sha256'],
             'AUTH_SNAPSHOT_SHA')
        if path.suffix == '.sqlite3':
            raw = p.raw_file(path)
            need(raw.startswith(b'SQLite format 3\0') and raw[18:20] == b'\x01\x01',
                 'AUTH_SNAPSHOT_JOURNAL')
        # 不允许额外 WAL/SHM 改写快照语义。
        need(not any(Path(str(path) + suffix).exists()
                     or Path(str(path) + suffix).is_symlink() for suffix in ('-wal', '-shm')),
             'AUTH_SNAPSHOT_SIDECAR')
        pins[name.replace('/', '_').replace('.sqlite3', '').replace('.json', '')] = row['sha256']
    return pins


def verify_provenance(private, saved, quarantine):
    source_gate.verify(quarantine)
    need(private['schema'] == 'kb-r5a-provenance-v1'
         and private['quarantine'] == saved['quarantine'] == quarantine, 'AUTH_QUARANTINE_CHANGED')
    expected = {(h['channel'], h['request_sha256'], h['prompt_sha256'],
                 len(h['source_keys']), b.digest(h)) for h in quarantine['holds']}
    def checklist(value):
        rows = value['unknown_checklist']
        need(len(rows) == 4, 'AUTH_UNKNOWN_CHECKLIST')
        return {(r['channel'], r['request_sha256'], r['prompt_sha256'],
                 r['source_count'], r['source_set_sha256']) for r in rows}
    need(checklist(private) == checklist(saved) == expected, 'AUTH_UNKNOWN_CHECKLIST')


def stable_units(ns, row):
    if len(ns['cpq_windows']._one_line(row['body_text'])) > 600:
        units = ns['_document_units'](row)
    else:
        units = [ns['_make_unit']('KL:' + row['evidence_id'], row['chatroom_id'], [{
            'evidence_id': row['evidence_id'], 'body': ns['cpq_windows']._one_line(row['body_text']),
            'at': ns['cpq_windows']._time(row)}])]
    for unit in units:
        unit['business_role'] = row['role']
        unit['live_increment'] = True
        unit['unit_sha256'] = b.sha(row['role'] + '\0' + unit['unit_sha256'])
    return units


class Backend:
    """真实盘点只读副本；测试替换盘点输入，不替换准入与审核稿验证。"""

    def provenance(self, ws, als):
        return provenance.analyze(ws, als)

    def candidates(self, ws, als, quarantine):
        ns = wechat.algorithms()
        wc = []
        with closing(read_only(ws / 'catalog.sqlite3')) as db, \
                closing(read_only(ws / 'calls.sqlite3')) as calls, \
                closing(read_only(ws / 'state.sqlite3')) as state:
            last = calls.execute('SELECT MAX(created_at) FROM calls').fetchone()[0]
            need(b.number(last), 'AUTH_HISTORY_BOUNDARY')
            observed = {}
            for table, role in (('kb_segment', 'supplier'), ('kb_learning_segment', 'learning')):
                for room in db.execute('SELECT DISTINCT chatroom_id FROM ' + table):
                    key = b.sha(room[0])
                    need(key not in observed or observed[key] == role, 'AUTH_ROLE_CONFLICT')
                    observed[key] = role
            learning = {r[0] for r in state.execute(
                "SELECT DISTINCT json_extract(observation_json,'$.classification_id') "
                "FROM channel_role_intake WHERE json_extract(observation_json,'$.registered_business_role')='learning'")}
            for r in db.execute('''SELECT e.*,r.channel_role,r.channel_id_sha256
                FROM channel_business_evidence e JOIN channel_role_registry r USING(classification_id)
                WHERE e.owner=r.owner AND e.source_scope=r.source_scope AND r.channel_kind='group'
                  AND r.channel_role IN ('CHANNEL_ROLE_SUPPLIER','CHANNEL_ROLE_NEUTRAL')
                  AND e.created_at>? ORDER BY e.evidence_id''', (last,)):
                row = dict(r, chatroom_id=r['channel_id_sha256'])
                row['role'] = 'supplier' if row['channel_role'] == 'CHANNEL_ROLE_SUPPLIER' else 'learning'
                if (observed.get(row['chatroom_id']) != row['role'] or row['role'] == 'learning'
                        and row['classification_id'] not in learning or not ns['_eligible'](row)):
                    continue
                if row['role'] == 'learning' and not (row['source_kind'] in ('message', 'historical_message')
                        and row['is_cost_price'] == 0 and row['price_kind'] == 'not_price'):
                    continue
                if not b.number(row['source_created_at']):
                    continue
                if len(ns['_document_units'](row)) >= ns['DOC_CHUNKS']:
                    continue
                try:
                    source_gate.check([row], quarantine)
                except ValueError:
                    continue  # 原盘点已排除的直接未知不加入1600候选。
                wc.append({'row': row, 'units': stable_units(ns, row), 'blocked': None})
        _, review = alibaba.analyze(als)
        wanted = {r['source_id']: r for r in review['backlog_review_template']['entries']}
        ac = []
        with closing(read_only(als / 'analysis.sqlite3')) as db:
            for r in db.execute('SELECT group_json FROM analyses WHERE version IN '
                                '(SELECT analysis_version FROM current_conversations) ORDER BY version'):
                group = json.loads(r['group_json'])
                for record in group['records']:
                    item = wanted.pop(record['inbound_id'], None)
                    if item is None:
                        continue
                    need(item['source_sha256'] == record['source_sha256']
                         and item['content_sha256'] == b.sha(record['body']), 'AUTH_SOURCE_CHANGED')
                    try:
                        source_gate.check([{'evidence_id': record['inbound_id'], 'body_text': record['body'],
                                            'source_sha256': record['source_sha256']}], quarantine)
                    except ValueError:
                        continue  # 与盘点一致：命中未知隔离的阿里来源不进入候选，也不得借别名重发。
                    ac.append({'group': group, 'record': record, 'blocked': None})
        need(not wanted, 'AUTH_CANDIDATE_MISSING')
        return wc, ac


def unknown_aliases(db, row, quarantine):
    if not row.get('message_id'):
        return
    originals = db.execute('SELECT evidence_id,body_text,source_sha256 FROM channel_business_evidence '
        'WHERE owner=? AND source_scope=? AND message_id=?',
        (row['owner'], row['source_scope'], row['message_id'])).fetchall()
    source_gate.check([dict(r) for r in originals], quarantine)


def alibaba_aliases(db, record, quarantine):
    need(bool(record.get('message_id')), 'SOURCE_MESSAGE_ID_REQUIRED')
    for row in db.execute('SELECT id,payload FROM inbound ORDER BY id'):
        payload = json.loads(row['payload'])
        if (payload.get('account'), payload.get('conversation_id'), payload.get('message_id')) == (
                record['account'], record['conversation_id'], record['message_id']):
            source_gate.check([{'evidence_id': row['id'], 'body_text': payload['content'],
                                'source_sha256': b.sha(row['payload'])}], quarantine)


def old_alibaba_request(db, group, record):
    for row in db.execute('SELECT plan_json,group_json FROM windows w JOIN analyses a ON a.version=w.version '
            'WHERE EXISTS(SELECT 1 FROM model_attempts m WHERE m.version=w.version AND m.window_index=w.window_index)'):
        prior = json.loads(row['group_json'])
        sources = {r['inbound_id']: r for r in prior['records']}
        for part in json.loads(row['plan_json'])['segments']:
            old = sources[part['inbound_id']]
            same_platform = old.get('message_id') and (prior['account'], prior['conversation_id'], old['message_id']) == (
                group['account'], group['conversation_id'], record['message_id'])
            if (part['inbound_id'] == record['inbound_id']
                    or part['source_sha256'] == record['source_sha256'] or same_platform):
                return True
    return False


def review_candidates(runtime, ws, als, after, quarantine, wc, ac):
    entries, excluded = [], []

    def reject(channel, source, code, units=1):
        excluded.append({'channel': channel, 'source_key_sha256': b.sha(source),
                         'reason': code, 'units': units})

    with closing(read_only(ws / 'catalog.sqlite3')) as db, \
            closing(read_only(ws / 'state.sqlite3')) as state:
        extract, ctx, current = runtime.wechat_rows(db, state)
        authorized = {}
        for r in current:
            row = dict(r)
            need(row['evidence_id'] not in authorized, 'AUTH_DUPLICATE_SOURCE')
            authorized[row['evidence_id']] = row
        for candidate in wc:
            row, units = candidate['row'], candidate['units']
            key = row['evidence_id']
            code = candidate.get('blocked')
            current_row = authorized.get(key)
            if code is None and current_row is None:
                code = 'AUTH_NOT_CURRENT'
            if code is None:
                role = 'learning' if current_row['chatroom_id'] in ctx.get('_learning', {}) else 'supplier'
                wanted_role = 'CHANNEL_ROLE_NEUTRAL' if role == 'learning' else 'CHANNEL_ROLE_SUPPLIER'
                if (role != row['role'] or current_row.get('role', role) != role
                        or current_row.get('channel_role', wanted_role) != wanted_role):
                    code = 'AUTH_ROLE_CONFLICT'
                elif (row['chatroom_id'] not in (current_row['chatroom_id'], b.sha(current_row['chatroom_id']))
                        or any(current_row[k] != row[k] for k in (*SOURCE_FIELDS, 'body_text')
                               if k != 'chatroom_id')):
                    code = 'SOURCE_CHANGED'
                elif not extract._eligible(current_row):
                    code = 'SOURCE_NOT_ELIGIBLE'
                elif row['source_created_at'] > after and row['created_at'] > after:
                    code = 'NATURAL_INCREMENT'
            if code is not None:
                reject('wechat', key, code, len(units))
                continue
            # 每个稳定单元再次经过来源/正文/提示词及平台别名的全局隔离。
            for unit in units:
                unit = dict(unit, chatroom_id=current_row['chatroom_id'])
                try:
                    source_gate.check([row], quarantine, b.sha(wechat_prompt(extract, unit)))
                    unknown_aliases(db, row, quarantine)
                except ValueError:
                    reject('wechat', unit['unit_id'], 'UNKNOWN_CROSS')
                    continue
                _, table = extract.kb_tables(unit) if hasattr(extract, 'kb_tables') else (
                    None, 'kb_learning_segment' if unit['business_role'] == 'learning' else 'kb_segment')
                prior = db.execute('SELECT unit_sha256,status FROM ' + table + ' WHERE unit_id=?',
                                   (unit['unit_id'],)).fetchone()
                if prior is not None:
                    reject('wechat', unit['unit_id'], 'PRIOR_UNIT_REVIEW_REQUIRED')
                    continue
                entries.append(wechat_entry(extract, unit, [current_row]))
    with closing(read_only(als / 'inbound.sqlite3')) as inbound, \
            closing(read_only(als / 'ledger.sqlite3')) as ledger, \
            closing(read_only(als / 'analysis.sqlite3')) as analysis:
        groups = runtime.alibaba_groups(inbound, ledger)
        current = {}
        for group in groups.values():
            for record in group['records']:
                need(record['inbound_id'] not in current, 'AUTH_DUPLICATE_SOURCE')
                current[record['inbound_id']] = (group, record)
        for candidate in ac:
            record = candidate['record']
            key = record['inbound_id']
            if candidate.get('blocked') or key not in current:
                reject('alibaba', key, candidate.get('blocked') or 'AUTH_NOT_CURRENT')
                continue
            group, live = current[key]
            if (live != runtime.core.normalize_record(record)
                    or any(group[k] != candidate['group'][k] for k in ('key', 'owner', 'source_version'))):
                reject('alibaba', key, 'SOURCE_CHANGED')
                continue
            source = {'evidence_id': key, 'body_text': record['body'], 'source_sha256': record['source_sha256']}
            try:
                source_gate.check([source], quarantine)
                alibaba_aliases(inbound, record, quarantine)
            except AuthorizationError:
                reject('alibaba', key, 'SOURCE_MESSAGE_ID_REQUIRED')
                continue
            except ValueError:
                reject('alibaba', key, 'UNKNOWN_CROSS')
                continue
            if old_alibaba_request(analysis, group, record):
                reject('alibaba', key, 'PRIOR_REQUEST_REVIEW_REQUIRED')
                continue
            # 计划异常不能降级成另一种分块；留人工，原授权异常在runtime全停。
            try:
                unit = single_alibaba_unit(runtime.core, group, live)
            except ValueError:
                reject('alibaba', key, 'SINGLE_UNIT_REVIEW_REQUIRED')
                continue
            try:
                source_gate.check([source], quarantine, unit['prompt_sha256'])
            except ValueError:
                reject('alibaba', key, 'UNKNOWN_CROSS')
                continue
            item = AlibabaAdapter._entry(SimpleNamespace(auth=runtime.auth), unit, live)
            if item['source_at'] is None:
                reject('alibaba', key, 'SOURCE_TIME_REQUIRED')
            elif item['source_at'] > after and item['admitted_at'] > after:
                reject('alibaba', key, 'NATURAL_INCREMENT')
            else:
                entries.append(item)
    return entries, excluded


def generate(session_path, after, *, writers_stopped=False):
    need(writers_stopped is True, 'AUTH_WRITERS_NOT_STOPPED')
    need(type(after) is int and 0 < after <= time.time(), 'AUTH_BOUNDARY')
    session = p.Session(session_path)
    backend = Backend()
    with session.locked():
        pins = snapshot_pins(session)
        ws, als = session.root / 'snapshot/wechat', session.root / 'snapshot/alibaba'
        quarantine = p.read_json(session.root / 'inventory/quarantine.json')
        saved = p.read_json(session.root / 'inventory/provenance.json')
        counts = p.read_json(session.root / 'inventory/summary.json')['counts']
        with quiet_readonly(session.root / 'snapshot'):
            _, private = backend.provenance(ws, als)
            verify_provenance(private, saved, quarantine)
            wc, ac = backend.candidates(ws, als, quarantine)
            need(counts['wechat_sources'] == len(wc)
                 and counts['wechat_units'] == sum(len(c['units']) for c in wc)
                 and counts['alibaba_sources'] == counts['alibaba_units'] == len(ac), 'AUTH_CANDIDATE_COUNT')
            with original_authorization(ws, als, after) as runtime:
                entries, excluded = review_candidates(runtime, ws, als, after, quarantine, wc, ac)
            review = {'schema': 'kb-backlog-review-v1', 'after': after, 'snapshots': pins, 'entries': entries}
            plan_review.validate_review(review, quarantine)
        need(snapshot_pins(session) == pins, 'AUTH_SNAPSHOT_SHA')
        output = Path(tempfile.mkdtemp(prefix='kb-authorization-review-', dir='/tmp'))
        try:
            # canonical 原字节无换行；文件SHA和 plan_review 返回的digest相同。
            p.write(output / 'review.json', b.canon(review).encode())
            p.write_json(output / 'quarantine.json', quarantine)
            p.write_json(output / 'exclusions.json', {'schema': 'kb-authorization-exclusions-v1',
                'review_sha256': b.digest(review), 'snapshots': pins, 'excluded': excluded})
            p.write_json(output / 'provenance.json', private)
            reasons = Counter()
            for row in excluded:
                reasons[row['reason']] += row['units']
            result = {'私有目录': str(output), 'review_sha256': p.sha(p.raw_file(output / 'review.json')),
                '候选单元数': len(entries), '排除候选数': sum(r['units'] for r in excluded),
                '理由计数': dict(sorted(reasons.items())),
                '快照文件数': len(pins), '未知请求数': 4, '模型请求': 0, '生产写入': 0}
            p.write_json(output / 'summary.json', result)
            return result
        except BaseException:
            shutil.rmtree(output)
            raise


def main(argv=None):
    parser = p.Parser(description=__doc__, add_help=False)
    parser._optionals.title = '选项'
    parser.add_argument('-h', '--help', action='help', help='显示运行说明后退出')
    parser.add_argument('--session', required=True, type=Path, help='R6-A 已通过 snapshot 和 inventory 的私有会话目录')
    parser.add_argument('--after', required=True, type=int, help='人工核准的固定 UTC 秒数，与 plan 保持一致')
    parser.add_argument('--writers-stopped', action='store_true', help='确认旧写者和 timer 已全部停止')
    args = parser.parse_args(argv)
    try:
        result = generate(args.session, args.after, writers_stopped=args.writers_stopped)
    except Exception as error:
        code = error.args[0] if isinstance(error, AuthorizationError) else 'AUTH_REVIEW_STOPPED'
        # 原异常即便带配置或身份也不回显；不产生失败审核稿、不重试。
        print(json.dumps({'错误代码': code}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
