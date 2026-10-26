# Review snapshot only: identity and content generalized; not a deployed service.
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import math
import os
if os.environ.get('EXAMPLE_POLICY_AUDIT_ONLY') != '1':
    raise RuntimeError('Reference snapshot is for audit only.')
from pathlib import Path
import re
import sqlite3
import stat
import time
import urllib.parse
KNOWN_WHATSAPP_CONFIG = Path(os.environ['EXAMPLE_WHATSAPP_CONFIG'])
KINDS = frozenset({'company_identity', 'product', 'supplier', 'quote', 'customer_requirement', 'faq', 'workflow', 'logistics', 'after_sales', 'other'})
MAX_BATCH = 200
MAX_SOURCE_CHARS = 32000
MAX_NOTE_PDF_SOURCE_CHARS = 64000
MAX_SPREADSHEET_SOURCE_CHARS = 200000
MAX_SUPPLIER_SOURCE_CHARS = 200000
MAX_NOTE_SOURCE_CHARS = 200000
MAX_NOTE_ASSET_ORDINAL = 2000
_PENDING = frozenset({'pending', 'processing', 'downloaded', 'processing_deferred'})
_ID = re.compile('[A-Za-z0-9_-]{1,64}\\Z', re.ASCII)
_KID = re.compile('K-[a-f0-9]{24}\\Z', re.ASCII)
_SECRET = re.compile('(?<![A-Za-z0-9_])(?:token|api[_-]?key|secret|password|authorization|aeskey)\\s*[:=]\\s*[^\\s,;，；]+|(?<![A-Za-z0-9_])Bearer\\s+[A-Za-z0-9._~+/=-]+|(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{16,}', re.I)
_PATH = re.compile('/(?:Users|private|var|tmp|home|Applications)/[^\\s，。；<>]+|[A-Za-z]:\\\\[^\\s，。；<>]+')
_TRANSPORT = re.compile('https?://[^\\s<>]+|(?<![A-Za-z0-9_])wxid_[A-Za-z0-9_-]+|[0-9]+@(?:chatroom|g\\.us|lid|s\\.whatsapp\\.net)', re.I)

def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def _clean(value, limit=6000, blocked=(), strip=True):
    if not isinstance(value, str):
        return ''
    value = ''.join((c for c in value if ord(c) >= 32 and ord(c) != 127 or c in '\n\t'))
    value = _SECRET.sub('[凭据已省略]', value)
    value = _PATH.sub('[本机路径已省略]', value)
    value = _TRANSPORT.sub('[链接或账号标识已省略]', value)
    for identity in sorted({v for v in blocked if isinstance(v, str) and v}, key=len, reverse=True):
        value = value.replace(identity, '[账号标识已省略]')
    return (value.strip() if strip else value)[:limit]

def _stamp(value):
    return type(value) in (int, float) and math.isfinite(value) and (0 < value <= 1000000000000.0)

def _settings(config):
    if not isinstance(config, dict):
        raise ValueError
    from owner_work_sharing import validate as sharing_validate
    sharing_validate(config)
    value = config.get('company_knowledge', {})
    if not isinstance(value, dict) or set(value) - {'enabled', 'company_id', 'company_name', 'active_after', 'whatsapp_config'}:
        raise ValueError
    if type(value.get('enabled', False)) is not bool:
        raise ValueError
    if not value.get('enabled', False):
        return None
    if not isinstance(value.get('company_id'), str) or not _ID.fullmatch(value['company_id']) or (not isinstance(value.get('company_name'), str)) or (not value['company_name'].strip()) or (len(value['company_name']) > 100) or any((ord(c) < 32 or ord(c) == 127 for c in value['company_name'])) or (not _stamp(value.get('active_after'))):
        raise ValueError
    if 'whatsapp_config' in value:
        path = value['whatsapp_config']
        if not isinstance(path, str) or Path(path) != KNOWN_WHATSAPP_CONFIG or '..' in Path(path).parts:
            raise ValueError
    return value

def settings(config):
    try:
        return _settings(config)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ValueError('company_config_invalid') from None

def initialize(store):
    with store.lock, store.db:
        store.db.executescript('\n        CREATE TABLE IF NOT EXISTS company_sources(\n          company_id TEXT NOT NULL,source_id TEXT PRIMARY KEY,platform TEXT NOT NULL,route TEXT NOT NULL,\n          origin_key TEXT NOT NULL,origin_json TEXT NOT NULL,visibility TEXT NOT NULL,personal_owner TEXT NOT NULL,\n          channel_name TEXT NOT NULL,created_at REAL NOT NULL,latest_version TEXT NOT NULL,needs_refresh INTEGER NOT NULL,\n          UNIQUE(company_id,platform,route,origin_key));\n        CREATE TABLE IF NOT EXISTS company_versions(\n          source_id TEXT NOT NULL,version_hash TEXT NOT NULL,verification_status TEXT NOT NULL,\n          created_at REAL NOT NULL,PRIMARY KEY(source_id,version_hash));\n        CREATE TABLE IF NOT EXISTS company_records(\n          source_id TEXT NOT NULL,version_hash TEXT NOT NULL,ordinal INTEGER NOT NULL,kind TEXT NOT NULL,\n          text TEXT NOT NULL,content_status TEXT NOT NULL,metadata_json TEXT NOT NULL,\n          PRIMARY KEY(source_id,version_hash,ordinal));\n        CREATE TABLE IF NOT EXISTS company_reviews(\n          id INTEGER PRIMARY KEY,company_id TEXT NOT NULL,source_id TEXT NOT NULL,version_hash TEXT NOT NULL,\n          action TEXT NOT NULL,reviewer_id TEXT NOT NULL,reviewed_at REAL NOT NULL,origin_json TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS company_sync_state(\n          company_id TEXT NOT NULL,route_key TEXT NOT NULL,last_rowid INTEGER NOT NULL,\n          PRIMARY KEY(company_id,route_key));\n        ')

def _table(db, name, columns):
    return columns.issubset({r[1] for r in db.execute('SELECT * FROM pragma_table_info(?)', (name,))})

def _owner_allowed(row, config, *, sealed=False, db=None):
    o = config.get('owner_control', {})
    sender = row.get('owner_wxid')
    members = {o.get('owner_wxid'), *o.get('group_member_wxids', [])}
    if o.get('enabled') is not True or row.get('access_kind') != 'work' or (not isinstance(row.get('profile_id'), str)) or (not row['profile_id']) or (not sealed and (row.get('app_id') != config.get('app_id') or row.get('own_wxid') != o.get('own_wxid'))) or (row.get('profile_id') != config.get('assistant_profile_id')) or (row.get('workspace_owner_wxid') != o.get('owner_wxid')) or (sender not in members):
        return False
    if row.get('channel_kind') == 'private':
        return o.get('private_enabled') is True and row.get('channel_id') == sender and (sender == o.get('owner_wxid') or o.get('private_members_enabled') is True)
    group = config.get('groups', {}).get(row.get('channel_id'), {})
    if not sealed and db is not None:
        from group_lifecycle import is_closed_db
        if is_closed_db(db, config, row.get('channel_id'), row.get('own_wxid')):
            return False
    return row.get('channel_kind') == 'group' and row.get('channel_id') in o.get('group_ids', []) and (group.get('enabled') is False) and (group.get('business_role') == 'internal')

def _group_allowed(db, row, config, *, sealed=False):
    g = config.get('groups', {}).get(row.get('group_id'), {})
    role = row.get('business_role')
    scope = row.get('scope_id')
    gid = row.get('group_id')
    if role == 'internal':
        from collection_policy import enabled, group_view
        owner = config.get('owner_control', {})
        after = g.get('activation_after')
        if not enabled(config) or owner.get('enabled') is not True or gid not in owner.get('group_ids', []) or (g.get('business_role') != 'internal') or (g.get('own_wxid') != owner.get('own_wxid')) or (not _stamp(after)) or (not _stamp(row.get('created_at'))) or (row['created_at'] < after):
            return False
        if sealed:
            if type(g.get('enabled')) is not bool or g.get('onboarding_pending_activation') or g.get('management_pending_activation'):
                return False
            g = dict(g, enabled=True)
        else:
            g = group_view(config, gid, db=db, app_id=row.get('app_id'), own_wxid=row.get('own_wxid'), created_at=row.get('created_at')) or {}
    if not sealed:
        from group_lifecycle import is_closed_db
        if is_closed_db(db, config, gid, row.get('own_wxid')):
            return False
    if g.get('enabled') is not True or role not in ('supplier', 'customer', 'internal') or g.get('business_role') != role or (not sealed and (row.get('app_id') != config.get('app_id') or row.get('own_wxid') != g.get('own_wxid'))) or (row.get('profile_id') != config.get('assistant_profile_id')) or (not scope) or (scope != g.get('memory_scope_id')):
        return False
    payload = json.loads(row.get('payload', '{}')) if 'payload' in row else None
    if payload is not None and (not isinstance(payload, dict) or payload.get('own_wxid') != row['own_wxid'] or row.get('sender_id') == row['own_wxid']):
        return False
    if sum((isinstance(v, dict) and v.get('memory_scope_id') == scope for v in config.get('groups', {}).values())) != 1:
        return False
    if not _table(db, 'memory_bindings', {'profile_id', 'scope_id', 'app_id', 'own_wxid', 'group_id', 'created_at'}):
        return False
    bindings = db.execute('SELECT app_id,own_wxid,group_id,created_at FROM memory_bindings WHERE profile_id=? AND scope_id=?', (row['profile_id'], scope)).fetchall()
    wires = {(b['app_id'], b['own_wxid'], b['group_id']) for b in bindings}
    return bool(bindings and all((b['group_id'] == gid for b in bindings)) and ((row['app_id'], row['own_wxid'], gid) in wires) and ((config.get('app_id'), g.get('own_wxid'), gid) in wires) and any(((b['app_id'], b['own_wxid'], b['group_id']) == (row['app_id'], row['own_wxid'], gid) and _stamp(b['created_at']) and (row.get('created_at', 0) >= int(b['created_at'])) for b in bindings)))

def _wa_open(settings):
    if not settings.get('whatsapp_config'):
        return (None, None)

    def private(path):
        for p in (path, *path.parents):
            if p.is_symlink():
                raise ValueError
        s = path.stat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 63:
            raise ValueError
    path = Path(settings['whatsapp_config'])
    private(path)
    if path.stat().st_size > 128 * 1024:
        raise ValueError
    config = json.loads(path.read_text())
    if not isinstance(config, dict) or config.get('enabled') is not True:
        return (None, None)
    if not isinstance(config.get('contacts', {}), dict) or not isinstance(config.get('groups', {}), dict):
        raise ValueError
    state = Path(config.get('state_file', ''))
    if not state.is_absolute() or '..' in state.parts or state.parent != path.parent:
        raise ValueError
    private(state)
    if os.path.samefile(state, path):
        raise ValueError
    for field in ('token_file', 'callback_path_file'):
        candidate = config.get(field)
        if isinstance(candidate, str) and (Path(candidate) == state or (Path(candidate).exists() and os.path.samefile(candidate, state))):
            raise ValueError
    db = sqlite3.connect('file:' + urllib.parse.quote(str(state), safe='/') + '?mode=ro', uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return (config, db)

def _wa_safe(settings):
    try:
        (config, db) = _wa_open(settings)
        return (config, db, 0)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, UnicodeError, RecursionError):
        return (None, None, 1)

def _wa_allowed(row, config):
    if not config or config.get('enabled') is not True:
        return False
    chat = row.get('chat')
    target = config.get('groups', {}).get(chat) if isinstance(chat, str) and chat.endswith('@g.us') else config.get('contacts', {}).get(chat)
    return bool(isinstance(target, dict) and target.get('enabled') is True and (row.get('channel') == config.get('channel_id')) and (row.get('profile') == config.get('profile_id')) and (row.get('scope') == target.get('scope_id')) and (row.get('direction') == 'user') and (row.get('business_role') in ('supplier', 'customer', 'internal')) and (row.get('business_role') == target.get('business_role', 'customer')))

def _kind(text, role):
    lower = text.casefold()
    for (kind, terms) in (('quote', ('报价', '单价', '底价', 'quotation', 'unit price', 'quote', 'usd', '￥')), ('after_sales', ('售后', '维修', '故障', 'repair', 'warranty', 'broken')), ('logistics', ('物流', '运费', '海运', '空运', 'shipping', 'freight', 'delivery')), ('workflow', ('流程', '步骤', 'sop', 'workflow', 'checklist')), ('customer_requirement', ('需求', '需要', 'requirement', 'we need')), ('product', ('灯具', '灯光', '参数', '型号', 'dmx', 'lighting', 'fixture', 'specification')), ('faq', ('常见问题', 'faq'))):
        if any((word in lower for word in terms)):
            return kind
    return 'supplier' if role == 'supplier' else 'other'

def _quote_metadata(kind):
    if kind != 'quote':
        return {}
    fields = ('product_model', 'supplier', 'recipient', 'price_kind', 'variant', 'quantity', 'unit_price', 'currency', 'tax', 'shipping', 'trade_terms', 'payment_terms', 'quoted_at', 'valid_until', 'minimum_order', 'lead_time')
    return {'fields': dict.fromkeys(fields), 'missing_fields': list(fields), 'extraction': 'not_extracted', 'price_currentness': 'not_verified_by_index', 'comparability': 'requires_matched_conditions', 'verification_scope': 'indexed_version_only'}

def _origin_key(route, origin):
    if route == 'supplier_directory':
        return _json([origin.get(k) for k in ('profile_id', 'source_key', 'root_hash', 'relative_path')])
    if route == 'wx_shared_work':
        return _json([origin.get(k) for k in ('app_id', 'message_id', 'material_id', 'note_asset_id')])
    if route == 'wx_group_note':
        return _json([origin.get(k) for k in ('app_id', 'message_id', 'group_id', 'scope_id', 'group_note_asset_id')])
    return _json([origin.get(k) for k in ('app_id', 'message_id', 'channel', 'id')])

def source_id_for(settings, platform, route, origin):
    origin_key = _origin_key(route, origin)
    return 'K-' + hashlib.sha256(_json([settings['company_id'], platform, route, origin_key]).encode()).hexdigest()[:24]

def _spreadsheet_part(route, part, origin=None):
    if part.get('status') != 'ready' or part.get('format') not in ('xlsx', 'xls'):
        return False
    if route == 'wx_group_note':
        return bool(isinstance(origin, dict) and origin.get('group_note_asset_kind') == 'file' and isinstance(origin.get('group_note_asset_id'), str) and re.fullmatch('GN-[a-f0-9]{24}', origin['group_note_asset_id']))
    if route == 'wx_shared_work' and isinstance(origin, dict) and origin.get('note_asset_id'):
        return bool(origin.get('note_asset_kind') == 'file' and isinstance(origin['note_asset_id'], str) and re.fullmatch('N-[a-f0-9]{16}', origin['note_asset_id']))
    return route in ('wx_group', 'wx_owner', 'wx_shared_work')

def _note_part(part):
    return part.get('status') == 'ready' and part.get('format') == 'wechat_note_inline'

def _source_budget(route, origin, parts):
    if route == 'supplier_directory':
        return MAX_SUPPLIER_SOURCE_CHARS
    if any((_spreadsheet_part(route, part, origin) for part in parts[:9])):
        return MAX_SPREADSHEET_SOURCE_CHARS
    if any((_note_part(part) for part in parts[:9])):
        return MAX_SOURCE_CHARS + MAX_NOTE_SOURCE_CHARS
    if any((_group_pdf_part(route, part) for part in parts[:9])):
        return MAX_SOURCE_CHARS + MAX_NOTE_PDF_SOURCE_CHARS + 201
    if route == 'wx_shared_work' and origin.get('note_asset_kind') == 'file' and isinstance(origin.get('note_asset_id'), str) and re.fullmatch('N-[a-f0-9]{16}', origin['note_asset_id']) and (len(parts) == 1) and (parts[0].get('format') == 'pdf'):
        return MAX_NOTE_PDF_SOURCE_CHARS
    if route == 'wx_group_note' and origin.get('group_note_asset_kind') == 'file' and isinstance(origin.get('group_note_asset_id'), str) and re.fullmatch('GN-[a-f0-9]{24}', origin['group_note_asset_id']) and (len(parts) == 1) and (parts[0].get('format') == 'pdf'):
        return MAX_NOTE_PDF_SOURCE_CHARS
    return MAX_SOURCE_CHARS

def _group_pdf_part(route, part):
    return route == 'wx_group' and part.get('status') == 'ready' and (part.get('format') == 'pdf')

def _group_index_provenance(part, blocked, index_start, prefix, body):
    from wechat_note_reader import safe_note_provenance
    provenance = safe_note_provenance(part.get('source_provenance', {}))
    if not provenance:
        return {}
    raw = part.get('text', '')
    if not isinstance(raw, str) or len(raw) > 24000:
        return {}
    cleaned = _clean(raw, MAX_SOURCE_CHARS, blocked, strip=False)
    leading = len(cleaned) - len(cleaned.lstrip())
    spans = []
    for item in provenance['items']:
        item['source_name'] = _clean(item['source_name'], 96, blocked)
        item['source_time'] = _clean(item['source_time'], 64, blocked)
        positions = []
        for key in ('text_start', 'text_end'):
            position = len(_clean(raw[:item[key]], MAX_SOURCE_CHARS, blocked, strip=False)) - leading
            positions.append(index_start + len(prefix) + max(0, min(len(body), position)))
        spans.append({'ordinal': item['ordinal'], 'start': positions[0], 'end': positions[1]})
    return {'source_provenance': provenance, 'source_provenance_index_spans': spans, 'source_attachment_ordinal': part.get('ordinal', 0), 'source_provenance_coordinates': 'original_inline_text', 'source_provenance_index_start': index_start + len(prefix)}

def _group_provenance_page(records, offset, size):
    from wechat_note_reader import safe_note_provenance
    result = []
    seen = set()
    remaining = 12
    for row in records:
        meta = json.loads(row['metadata_json'])
        provenance = safe_note_provenance(meta.get('source_provenance', {}))
        start = meta.get('source_provenance_index_start')
        ordinal = meta.get('source_attachment_ordinal')
        spans = meta.get('source_provenance_index_spans')
        if not provenance or type(start) is not int or (not 0 <= start <= MAX_SPREADSHEET_SOURCE_CHARS) or (type(ordinal) is not int) or (not 0 <= ordinal < 8) or (not isinstance(spans, list)) or (len(spans) != len(provenance['items'])) or ((ordinal, start) in seen):
            continue
        selected = set()
        for (item, span) in zip(provenance['items'], spans):
            if not isinstance(span, dict) or span.get('ordinal') != item['ordinal'] or any((type(span.get(key)) is not int or not start <= span[key] <= MAX_SPREADSHEET_SOURCE_CHARS for key in ('start', 'end'))) or (span['start'] > span['end']):
                break
            if span['end'] > offset and span['start'] < offset + size or (offset <= start < offset + size and item['status'] not in ('extracted', 'text_truncated')):
                selected.add(item['ordinal'])
        else:
            seen.add((ordinal, start))
            for item in reversed(provenance['items']):
                if item['ordinal'] in selected and item.get('parent_ordinal'):
                    selected.add(item['parent_ordinal'])
            visible = [item for item in provenance['items'] if item['ordinal'] in selected]
            if not visible:
                continue
            kept = visible[:remaining]
            remaining -= len(kept)
            page = dict(provenance, items=kept, items_scope='current_index_page', items_in_page=len(visible), items_omitted=len(visible) - len(kept), includes_required_ancestors=any((item['status'] == 'record_expanded' for item in kept)))
            result.append({'attachment_ordinal': ordinal, 'provenance': page, 'coordinates': 'original_inline_text', 'indexed_page_offset': offset, 'indexed_page_chars': size})
    return result

def _save(db, settings, platform, route, origin, parts, now, visibility='shared', personal_owner='', name='', seed=False):
    origin = {key: value for (key, value) in origin.items() if key in {'_rowid', 'app_id', 'message_id', 'own_wxid', 'owner_wxid', 'sender_id', 'profile_id', 'workspace_owner_wxid', 'channel_kind', 'channel_id', 'created_at', 'access_kind', 'group_id', 'scope_id', 'business_role', 'channel', 'id', 'profile', 'scope', 'chat', 'sender', 'created', 'direction', 'source_key', 'root_hash', 'relative_path', 'supplier_name', 'file_sha256', 'file_size', 'modified_at', 'material_id', 'source_rowid', 'source_scope', 'source_text_sha256', 'note_asset_id', 'parent_xml_sha256', 'parent_source_version', 'note_asset_ordinal', 'note_asset_kind', 'group_note_asset_id', 'group_note_parent_ordinal', 'group_note_asset_ordinal', 'group_note_asset_kind'}}
    origin_key = _origin_key(route, origin)
    sid = source_id_for(settings, platform, route, origin)
    blocked = [origin.get(k) for k in ('app_id', 'own_wxid', 'group_id', 'channel_id', 'owner_wxid', 'sender_id', 'chat', 'sender', 'channel')]
    records = []
    remaining = _source_budget(route, origin, parts)
    refresh = False
    index_start = 0
    spreadsheet_source = any((_spreadsheet_part(route, part, origin) for part in parts[:9]))
    group_pdf_source = any((_group_pdf_part(route, part) for part in parts[:9]))
    note_source = any((_note_part(part) for part in parts[:9]))
    ordinary_remaining = MAX_SOURCE_CHARS if spreadsheet_source or group_pdf_source or note_source else remaining
    for part in parts[:9]:
        status = part.get('status', 'ready')
        refresh = refresh or status in _PENDING
        status = status if isinstance(status, str) and re.fullmatch('[a-z][a-z0-9_]{0,63}', status) else 'invalid_material'
        spreadsheet = _spreadsheet_part(route, part, origin)
        group_pdf = _group_pdf_part(route, part)
        note = _note_part(part)
        part_budget = min(remaining, MAX_NOTE_PDF_SOURCE_CHARS + 201) if group_pdf else min(remaining, MAX_NOTE_SOURCE_CHARS + 201) if note else remaining if spreadsheet else min(remaining, ordinary_remaining)
        if part_budget <= 0:
            continue
        body = _clean(part.get('text', ''), part_budget, blocked) if status == 'ready' else ''
        filename = _clean(part.get('name', ''), 200, blocked)
        prefix = '资料：' + filename + '\n' if filename else ''
        text = prefix + body
        if not text.strip():
            text = '[资料状态：' + status + '；没有可用正文]'
        text = text[:part_budget]
        remaining -= len(text)
        if not spreadsheet and (not group_pdf) and (not note):
            ordinary_remaining -= len(text)
        if not text:
            break
        kind = 'company_identity' if seed else _kind(text, origin.get('business_role', 'internal'))
        meta = _quote_metadata(kind)
        fmt = part.get('format', 'text')
        meta.update(material=bool(filename), truncated=bool(part.get('truncated') or len(text) == part_budget), format=fmt if isinstance(fmt, str) and re.fullmatch('[a-z0-9_]{1,64}', fmt) else 'unknown')
        if route == 'supplier_directory':
            meta.update(file_sha256=origin.get('file_sha256', ''), file_size=origin.get('file_size'), source_modified_at=origin.get('modified_at'))
        if route == 'wx_shared_work':
            meta.update(source_material_id=origin['material_id'], source_text_sha256=origin['source_text_sha256'], source_business_role=origin.get('business_role', 'unknown'), file_sha256=origin.get('file_sha256', ''), parent_xml_sha256=origin.get('parent_xml_sha256', ''), note_asset_id=origin.get('note_asset_id'), parent_source_version=origin.get('parent_source_version'), note_asset_ordinal=origin.get('note_asset_ordinal'), note_asset_kind=origin.get('note_asset_kind'), source_provenance=part.get('source_provenance', {}), work_classifications=part.get('work_classifications', []))
        if route == 'wx_group_note':
            meta.update(group_note_asset_id=origin['group_note_asset_id'], group_note_parent_ordinal=origin['group_note_parent_ordinal'], group_note_asset_ordinal=origin['group_note_asset_ordinal'], group_note_asset_kind=origin['group_note_asset_kind'], parent_xml_sha256=origin['parent_xml_sha256'], file_sha256=origin.get('file_sha256', ''), source_text_sha256=origin.get('source_text_sha256', ''))
        if route == 'wx_group':
            reference_provenance = _safe_reference_provenance(part.get('reference_provenance'))
            if reference_provenance:
                meta['reference_provenance'] = reference_provenance
                meta.update(source_attachment_ordinal=part.get('ordinal'), reference_text_sha256=part.get('reference_text_sha256'), reference_revision=part.get('reference_revision'))
            lineage = _group_index_provenance(part, blocked, index_start, prefix[:len(text)], text[len(prefix):]) if status == 'ready' else {}
            if lineage:
                meta.update(lineage)
                if lineage['source_provenance'].get('record_fully_read') is not True:
                    meta['truncated'] = True
            elif fmt in ('wechat_note_inline', 'wechat_record_stub'):
                meta.update(forward_structure_status='unavailable', truncated=True)
        for start in range(0, len(text), 4000):
            chunk_meta = dict(meta, indexed_text_start=index_start + start) if route == 'wx_group' else meta
            records.append((kind, text[start:start + 4000], status, chunk_meta))
        index_start += len(text)
        if remaining <= 0:
            break
    if not records:
        records = [('other', '[无可用正文]', 'empty', {})]
    version = hashlib.sha256(_json([records, origin.get('file_sha256')]).encode()).hexdigest() if route == 'supplier_directory' else hashlib.sha256(_json(records).encode()).hexdigest()
    existing = db.execute('SELECT latest_version FROM company_sources WHERE source_id=?', (sid,)).fetchone()
    if existing and existing['latest_version'] == version:
        return 0
    created = origin.get('created_at', origin.get('created', now))
    db.execute('INSERT INTO company_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id)\n        DO UPDATE SET latest_version=excluded.latest_version,needs_refresh=excluded.needs_refresh,origin_json=excluded.origin_json', (settings['company_id'], sid, platform, route, origin_key, _json(origin), visibility, personal_owner, _clean(name, 100, blocked), created, version, int(refresh)))
    db.execute('INSERT OR IGNORE INTO company_versions VALUES(?,?,?,?)', (sid, version, 'confirmed' if seed else 'candidate', now))
    for (ordinal, (kind, text, status, metadata)) in enumerate(records):
        db.execute('INSERT OR IGNORE INTO company_records VALUES(?,?,?,?,?,?,?)', (sid, version, ordinal, kind, text, status, _json(metadata)))
    return 1

def _seeds(db, s, now):
    if s['company_id'] != os.environ.get('EXAMPLE_COMPANY_ID', 'example-company') or s['company_name'] != os.environ.get('EXAMPLE_COMPANY_NAME', '示例公司'):
        return 0
    return sum((_save(db, s, 'user_confirmed', 'company_identity', {'message_id': 'identity-' + str(i), 'created_at': s['active_after']}, [{'text': text, 'status': 'ready'}], now, name='用户确认的公司定位', seed=True) for (i, text) in enumerate(('示例公司介绍（合成）。', '示例业务描述（合成）。'))))

def _row_batch(db, store_db, s, route, table, columns, query, params=()):
    if not _table(db, table, columns):
        return []
    key = route + ':' + hashlib.sha256(_json(params).encode()).hexdigest()[:16]
    state = store_db.execute('SELECT last_rowid FROM company_sync_state WHERE company_id=? AND route_key=?', (s['company_id'], key)).fetchone()
    cursor = state[0] if state else 0
    pending = store_db.execute('SELECT origin_json FROM company_sources WHERE company_id=? AND route=? AND needs_refresh=1 LIMIT 50', (s['company_id'], route)).fetchall()
    pending_rows = []
    for p in pending:
        old = json.loads(p[0])
        rowid = old.get('_rowid')
        if type(rowid) is int:
            row = db.execute(query + ' AND m.rowid=?', (*params, rowid)).fetchone()
            if row:
                pending_rows.append(dict(row))
    rows = [dict(r) for r in db.execute(query + ' AND m.rowid>? ORDER BY m.rowid LIMIT ?', (*params, cursor, MAX_BATCH - len(pending_rows)))]
    if rows:
        store_db.execute('INSERT OR REPLACE INTO company_sync_state VALUES(?,?,?)', (s['company_id'], key, max((r['_rowid'] for r in rows))))
    seen = set()
    return [r for r in pending_rows + rows if not (r['_rowid'] in seen or seen.add(r['_rowid']))]

def _wx_owner_parts(db, row):
    parts = [{'text': row.get('text', ''), 'status': 'ready'}]
    if _table(db, 'owner_materials', {'app_id', 'message_id', 'name', 'status', 'text', 'truncated', 'ordinal'}):
        fmt = 'format' if _table(db, 'owner_materials', {'format'}) else "'unknown' AS format"
        parts.extend((dict(r) for r in db.execute('SELECT name,status,text,truncated,' + fmt + ' FROM owner_materials WHERE app_id=? AND message_id=? ORDER BY ordinal LIMIT 8', (row['app_id'], row['message_id']))))
    return parts

def _shared_classifications(db, row):
    if not _table(db, 'owner_material_catalog', {'scope_key', 'material_id', 'source_version', 'source_rowid', 'record_key', 'revision', 'payload_json'}):
        return []
    rows = db.execute('SELECT c.payload_json FROM owner_material_catalog c\n        WHERE c.scope_key=? AND c.material_id=? AND c.source_version=? AND c.source_rowid=?\n        AND NOT EXISTS (SELECT 1 FROM owner_material_catalog newer\n            WHERE newer.scope_key=c.scope_key AND newer.record_key=c.record_key AND newer.revision>c.revision)\n        ORDER BY c.record_key LIMIT 8', (row['source_scope'], row['material_id'], row['grant_version_hash'], row['source_rowid'])).fetchall()
    result = []
    for record in rows:
        value = json.loads(record['payload_json'])
        if not isinstance(value, dict) or value.get('business_role') not in ('customer', 'supplier', 'unknown') or value.get('supplier_type') not in ('product', 'logistics', 'unknown') or (value.get('material_category') not in ('product', 'quote', 'logistics', 'requirement', 'contract', 'conversation', 'other')) or (not isinstance(value.get('contact_labels'), list)) or (len(value['contact_labels']) > 12):
            continue
        blocked = (row['owner_wxid'], row['own_wxid'], row['app_id'], row['channel_id'])
        result.append({key: value[key] for key in ('business_role', 'supplier_type', 'material_category')})
        result[-1].update({key: _clean(value.get(key, ''), maximum, blocked) for (key, maximum) in (('organization_label', 120), ('factory_label', 120), ('business_time', 120), ('summary', 1200), ('evidence_quote', 500))})
        result[-1].update(contact_labels=[_clean(item, 96, blocked) for item in value['contact_labels'] if isinstance(item, str)], classification_status='candidate', identity_status='unverified_labels')
    return result

def _shared_parts(db, row):
    from wechat_note_reader import safe_note_provenance
    classifications = _shared_classifications(db, row)
    provenance = safe_note_provenance(row.get('provenance_json', '{}'), offset=0, size=24000)
    parts = [{'name': row['name'], 'text': row['text'], 'status': row['status'], 'format': row['format'], 'truncated': row['truncated'], 'source_provenance': provenance, 'work_classifications': classifications}]
    for value in classifications:
        parts.append({'name': '上传人资料分类候选', 'text': _json(value)[:1000], 'status': 'ready', 'format': 'classification_candidate'})
    return parts

def _shared_business_role(db, row):
    if not _table(db, 'owner_material_catalog', {'scope_key', 'material_id', 'source_version', 'source_rowid', 'record_key', 'revision', 'business_role', 'payload_json'}):
        return 'unknown'
    roles = {value[0] for value in db.execute("SELECT DISTINCT c.business_role FROM owner_material_catalog c\n        WHERE c.scope_key=? AND c.material_id=? AND c.source_version=? AND c.source_rowid=?\n        AND c.business_role IN ('customer','supplier') AND json_valid(c.payload_json)\n        AND json_extract(c.payload_json,'$.business_role')=c.business_role\n        AND NOT EXISTS (SELECT 1 FROM owner_material_catalog newer\n            WHERE newer.scope_key=c.scope_key AND newer.record_key=c.record_key AND newer.revision>c.revision)\n        LIMIT 2", (row['source_scope'], row['material_id'], row['grant_version_hash'], row['source_rowid']))}
    return next(iter(roles)) if len(roles) == 1 else 'unknown'

def _shared_cursor_key(config):
    return 'wx_shared_work:' + hashlib.sha256(_json(config.get('assistant_profile_id')).encode()).hexdigest()[:16]

def _shared_rows(db, s, config):
    from owner_work_sharing import settings as sharing_settings
    if not sharing_settings(config) or not _table(db, 'owner_work_material_grants', {'material_id', 'status'}):
        return []
    key = _shared_cursor_key(config)
    state = db.execute('SELECT last_rowid FROM company_sync_state WHERE company_id=? AND route_key=?', (s['company_id'], key)).fetchone()
    cursor = state[0] if state else 0
    rows = db.execute("SELECT rowid AS _rowid,material_id FROM owner_work_material_grants WHERE status='active' AND rowid>? ORDER BY rowid LIMIT ?", (cursor, MAX_BATCH)).fetchall()
    if not rows and cursor:
        rows = db.execute("SELECT rowid AS _rowid,material_id FROM owner_work_material_grants WHERE status='active' ORDER BY rowid LIMIT ?", (MAX_BATCH,)).fetchall()
    if rows:
        db.execute('INSERT OR REPLACE INTO company_sync_state VALUES(?,?,?)', (s['company_id'], key, rows[-1]['_rowid']))
    return rows

def _shared_children(db, parent, asset_id=None):
    if not isinstance(parent.get('parent_xml_sha256'), str) or not re.fullmatch('[a-f0-9]{64}', parent['parent_xml_sha256']) or (not _table(db, 'owner_note_assets', {'parent_material_id', 'asset_id', 'ordinal', 'kind', 'name', 'parent_xml_sha256', 'status', 'text', 'format', 'truncated', 'archive_status', 'archive_sha256', 'archive_bytes'})):
        return []
    from owner_note_assets import safe_shared_assets
    rows = safe_shared_assets(db, parent['material_id'], parent['parent_xml_sha256'])
    result = []
    for row in rows:
        expected = 'N-' + hashlib.sha256((parent['material_id'] + '\x00' + str(row['ordinal'])).encode()).hexdigest()[:16]
        if row['status'] != 'ready' or row['archive_status'] != 'ready' or (asset_id is not None and row['asset_id'] != asset_id) or (type(row['ordinal']) is not int) or (not 1 <= row['ordinal'] <= MAX_NOTE_ASSET_ORDINAL) or (row['asset_id'] != expected) or (row['parent_xml_sha256'] != parent['parent_xml_sha256']) or (row['kind'] not in ('image', 'file')) or (not isinstance(row['text'], str)) or (not isinstance(row['archive_sha256'], str)) or (not re.fullmatch('[a-f0-9]{64}', row['archive_sha256'])) or (type(row['archive_bytes']) is not int) or (row['archive_bytes'] <= 0):
            continue
        result.append(dict(row))
    return result

def _sync_shared_work(db, s, config, now):
    from owner_work_sharing import shared_material
    scanned = updated = 0
    last_cursor = None
    for grant in _shared_rows(db, s, config):
        row = shared_material(db, config, grant['material_id'])
        if row is None or not _stamp(row.get('created_at')) or row['created_at'] < s['active_after']:
            scanned += 1
            last_cursor = grant['_rowid']
            continue
        children = _shared_children(db, row)
        if scanned and scanned + 1 + len(children) > MAX_BATCH:
            break
        scanned += 1 + len(children)
        last_cursor = grant['_rowid']
        parts = _shared_parts(db, row)
        row = dict(row, _rowid=grant['_rowid'], source_text_sha256=row['grant_version_hash'], file_sha256=row.get('sha256', ''), business_role=_shared_business_role(db, row))
        updated += _save(db, s, 'wechat', 'wx_shared_work', row, parts, now, name='共享工作资料')
        for child in children:
            origin = dict(row, note_asset_id=child['asset_id'], note_asset_ordinal=child['ordinal'], note_asset_kind=child['kind'], parent_xml_sha256=child['parent_xml_sha256'], parent_source_version=row['grant_version_hash'], file_sha256=child['archive_sha256'], source_text_sha256=hashlib.sha256(child['text'].encode()).hexdigest())
            updated += _save(db, s, 'wechat', 'wx_shared_work', origin, [{'name': child['name'], 'text': child['text'], 'status': 'ready', 'format': child['format'], 'truncated': child['truncated']}], now, name='共享笔记附件')
    if last_cursor is not None:
        db.execute('INSERT OR REPLACE INTO company_sync_state VALUES(?,?,?)', (s['company_id'], _shared_cursor_key(config), last_cursor))
    return (scanned, updated)

def _shared_visible(store, config, origin, requester):
    from owner_work_sharing import shared_material
    current = shared_material(store.db, config, origin.get('material_id'), requester=requester)
    if current is None:
        return False
    if not all((current.get(key) == origin.get(key) for key in ('app_id', 'message_id', 'own_wxid', 'owner_wxid', 'profile_id', 'workspace_owner_wxid', 'channel_kind', 'channel_id', 'access_kind', 'source_rowid', 'source_scope'))):
        return False
    if origin.get('note_asset_id'):
        children = _shared_children(store.db, current, origin['note_asset_id'])
        return bool(children and children[0]['parent_xml_sha256'] == origin.get('parent_xml_sha256') and (children[0]['ordinal'] == origin.get('note_asset_ordinal')) and (children[0]['kind'] == origin.get('note_asset_kind')))
    return True

def _shared_metadata(db, config, origin, requester, version_metadata):
    from owner_work_sharing import shared_material
    current = shared_material(db, config, origin['material_id'], requester=requester)
    if current is None:
        raise ValueError
    result = {'uploader': '本人上传' if current['owner_wxid'] == requester['owner_wxid'] else '同事上传', 'source_material_id': origin['material_id'], 'business_role': version_metadata.get('source_business_role', 'unknown'), 'shared_source_version': version_metadata.get('source_text_sha256', ''), 'file_sha256': version_metadata.get('file_sha256', ''), 'parent_xml_sha256': version_metadata.get('parent_xml_sha256', ''), 'share_authorization': current['share_authorization'], 'classification_status': 'candidate', 'identity_status': 'unverified_labels', 'source_provenance': version_metadata.get('source_provenance', {}), 'work_classifications': version_metadata.get('work_classifications', [])}
    if origin.get('note_asset_id'):
        result.update(note_asset_id=origin['note_asset_id'], note_asset_ordinal=origin['note_asset_ordinal'], note_asset_kind=origin['note_asset_kind'], parent_source_version=version_metadata.get('parent_source_version', ''))
    return result

def _safe_reference_provenance(value):
    if not isinstance(value, dict):
        return {}
    link = value.get('reference_link', {})
    if not isinstance(link, dict) or link.get('authority') != 'quoted_data_only':
        return {}
    clean = {key: link[key] for key in ('state', 'reason', 'authority', 'target_status', 'target_format') if isinstance(link.get(key), str) and re.fullmatch('[a-z][a-z0-9_]{0,63}', link[key])}
    clean.update({key: link[key] for key in ('target_truncated', 'reference_text_clipped', 'original_verified') if type(link.get(key)) is bool})
    status = value.get('reference_status')
    return {'reference_status': status if status in ('same_group_original_linked', 'same_group_original_unavailable', 'text_available', 'empty_text', 'quoted_media_unparsed') else 'unknown', 'reference_link': clean}

def _group_reference_current(db, row, config, now):
    ordinals = set()
    if _table(db, 'company_media', {'app_id', 'message_id', 'ordinal', 'format'}):
        ordinals.update((found[0] for found in db.execute("SELECT ordinal FROM company_media WHERE app_id=? AND message_id=? AND format='wechat_reference' LIMIT 9", (row['app_id'], row['message_id']))))
    if _table(db, 'company_reference_media_links', {'app_id', 'message_id', 'ordinal', 'state'}):
        ordinals.update((found[0] for found in db.execute('SELECT ordinal FROM company_reference_media_links WHERE app_id=? AND message_id=? LIMIT 9', (row['app_id'], row['message_id']))))
    if not ordinals:
        return True
    if len(ordinals) > 8 or any((type(ordinal) is not int or not 0 <= ordinal < 8 for ordinal in ordinals)):
        return False
    from company_reference_media import reference_material_current
    for ordinal in ordinals:
        if not reference_material_current(db, row['app_id'], row['message_id'], ordinal, config, now):
            return False
    return True

def _group_reference_version_current(db, source, version, config, now, records=None):
    if source['route'] != 'wx_group':
        return True
    origin = json.loads(source['origin_json'])
    current = {}
    if records is None:
        records = db.execute('SELECT metadata_json FROM company_records WHERE source_id=? AND version_hash=?', (source['source_id'], version)).fetchall()
    from company_reference_media import reference_material_part
    for record in records:
        meta = json.loads(record['metadata_json'])
        if meta.get('format') != 'wechat_reference' and 'reference_provenance' not in meta:
            continue
        ordinal = meta.get('source_attachment_ordinal')
        digest = meta.get('reference_text_sha256')
        revision = meta.get('reference_revision')
        if type(ordinal) is not int or not 0 <= ordinal < 8 or type(revision) is not int or (revision < 0) or (not isinstance(digest, str)) or (not re.fullmatch('[a-f0-9]{64}', digest)):
            return False
        if ordinal not in current:
            part = reference_material_part(db, origin['app_id'], origin['message_id'], ordinal, config, now)
            current[ordinal] = part.get('reference_provenance', {}) if part else {}
        if current[ordinal].get('reference_text_sha256') != digest or current[ordinal].get('reference_revision') != revision:
            return False
    return True

def _wx_group_parts(db, row, config=None, now=None):
    payload = json.loads(row['payload'])
    parts = [{'text': payload.get('text', ''), 'status': 'ready'}]
    attachments = payload.get('attachments', [])
    media = {}
    if _table(db, 'company_media', {'app_id', 'message_id', 'ordinal', 'name', 'status', 'text', 'format', 'truncated'}):
        provenance = 'provenance_json' if _table(db, 'company_media', {'provenance_json'}) else "'{}' AS provenance_json"
        media = {r['ordinal']: dict(r) for r in db.execute('SELECT ordinal,name,status,text,format,truncated,' + provenance + ' FROM company_media\n            WHERE app_id=? AND message_id=? ORDER BY ordinal LIMIT 8', (row['app_id'], row['message_id']))}
        from wechat_note_reader import safe_note_provenance
        for (ordinal, part) in media.items():
            part['source_provenance'] = safe_note_provenance(part.pop('provenance_json'))
            if part.get('format') == 'wechat_reference' and config is not None:
                from company_reference_media import reference_material_part
                current = reference_material_part(db, row['app_id'], row['message_id'], ordinal, config, now)
                if current is None:
                    part.update(status='source_revoked', text='', truncated=True)
                else:
                    part.update(current)
                    part['reference_text_sha256'] = current.get('reference_provenance', {}).get('reference_text_sha256')
                    part['reference_revision'] = current.get('reference_provenance', {}).get('reference_revision')
                    part['reference_provenance'] = _safe_reference_provenance(current.get('reference_provenance'))
    for (index, a) in enumerate(attachments[:8] if isinstance(attachments, (list, tuple)) else []):
        if index in media:
            parts.append(media.pop(index))
        elif isinstance(a, dict):
            parts.append({'name': a.get('name', '附件'), 'status': 'pending', 'text': ''})
    parts.extend(media.values())
    return parts

def _sync_group_notes(db, s, config, now):
    if not _table(db, 'company_note_assets', {'app_id', 'message_id', 'asset_id', 'parent_ordinal', 'ordinal', 'status'}):
        return (0, 0)
    key = 'wx_group_note:' + hashlib.sha256(_json(config.get('assistant_profile_id')).encode()).hexdigest()[:16]
    state = db.execute('SELECT last_rowid FROM company_sync_state WHERE company_id=? AND route_key=?', (s['company_id'], key)).fetchone()
    cursor = state[0] if state else 0
    query = 'SELECT a.rowid AS _rowid,a.app_id,a.message_id,a.asset_id FROM company_note_assets a\n        JOIN messages m ON m.app_id=a.app_id AND m.message_id=a.message_id\n        JOIN message_scopes ms ON ms.app_id=m.app_id AND ms.message_id=m.message_id\n        WHERE ms.profile_id=? AND m.created_at>=?'
    rows = db.execute(query + ' AND a.rowid>? ORDER BY a.rowid LIMIT ?', (config.get('assistant_profile_id'), s['active_after'], cursor, MAX_BATCH)).fetchall()
    if not rows and cursor:
        rows = db.execute(query + ' ORDER BY a.rowid LIMIT ?', (config.get('assistant_profile_id'), s['active_after'], MAX_BATCH)).fetchall()
    from company_note_assets import safe_assets
    scanned = updated = 0
    for asset in rows:
        scanned += 1
        current = db.execute('SELECT m.*,ms.profile_id,ms.scope_id,ms.own_wxid,ms.business_role FROM messages m\n            JOIN message_scopes ms ON ms.app_id=m.app_id AND ms.message_id=m.message_id\n            WHERE m.app_id=? AND m.message_id=?', (asset['app_id'], asset['message_id'])).fetchone()
        if current is None or not _group_allowed(db, dict(current), config):
            continue
        children = safe_assets(db, asset['app_id'], asset['message_id'], asset_id=asset['asset_id'])
        if len(children) != 1:
            continue
        child = children[0]
        origin = dict(current, _rowid=asset['_rowid'], group_note_asset_id=child['asset_id'], group_note_parent_ordinal=child['parent_ordinal'], group_note_asset_ordinal=child['ordinal'], group_note_asset_kind=child['kind'], parent_xml_sha256=child['parent_xml_sha256'], file_sha256=child['archive_sha256'], source_text_sha256=hashlib.sha256(child['text'].encode()).hexdigest())
        updated += _save(db, s, 'wechat', 'wx_group_note', origin, [{'name': child['name'], 'text': child['text'], 'status': child['status'], 'format': child['format'], 'truncated': child['truncated']}], now, name=config['groups'][current['group_id']].get('group_name', '已接入信息群'))
    if rows:
        db.execute('INSERT OR REPLACE INTO company_sync_state VALUES(?,?,?)', (s['company_id'], key, rows[-1]['_rowid']))
    return (scanned, updated)

def _wa_parts(db, row):
    parts = [{'text': row.get('text', ''), 'status': 'ready'}]
    for (table, textcol) in (('audio_records', 'transcript'), ('attachment_records', 'text')):
        if not _table(db, table, {'channel', 'message_id', 'metadata', 'status', textcol, 'truncated'}):
            continue
        for r in db.execute('SELECT metadata,status,' + textcol + ' AS text,truncated FROM ' + table + ' WHERE channel=? AND message_id=?', (row['channel'], row['id'])):
            metadata = json.loads(r['metadata'])
            parts.append({'name': metadata.get('name', '语音' if table == 'audio_records' else '附件'), 'text': r['text'], 'status': r['status'], 'truncated': r['truncated']})
    return parts

def sync_sources(store, config, config_path, now=None):
    wa_db = None
    try:
        s = _settings(config)
        if s is None:
            return {'status': 'disabled', 'scanned': 0, 'updated': 0}
        now = time.time() if now is None else now
        if not _stamp(now):
            raise ValueError
        initialize(store)
        scanned = updated = 0
        with store.lock, store.db:
            db = store.db
            updated += _seeds(db, s, now)
            rows = _row_batch(db, db, s, 'wx_owner', 'owner_messages', {'app_id', 'message_id', 'profile_id', 'workspace_owner_wxid', 'access_kind'}, 'SELECT m.rowid AS _rowid,m.* FROM owner_messages m WHERE m.profile_id=? AND m.created_at>=?', (config.get('assistant_profile_id'), s['active_after']))
            for row in rows:
                scanned += 1
                if not _stamp(row.get('created_at')) or row['created_at'] < s['active_after'] or (not _owner_allowed(row, config, db=db)):
                    continue
                private = row['channel_kind'] == 'private'
                updated += _save(db, s, 'wechat', 'wx_owner', row, _wx_owner_parts(db, row), now, 'personal' if private else 'shared', row['owner_wxid'] if private else '', '个人私聊' if private else config['groups'][row['channel_id']].get('group_name', '内部工作群'))
            (shared_scanned, shared_updated) = _sync_shared_work(db, s, config, now)
            scanned += shared_scanned
            updated += shared_updated
            (note_scanned, note_updated) = _sync_group_notes(db, s, config, now)
            scanned += note_scanned
            updated += note_updated
            if _table(db, 'message_scopes', {'profile_id', 'scope_id', 'own_wxid', 'business_role'}):
                rows = _row_batch(db, db, s, 'wx_group', 'messages', {'app_id', 'message_id', 'group_id', 'payload'}, 'SELECT m.rowid AS _rowid,m.*,s.profile_id,s.scope_id,s.own_wxid,s.business_role FROM messages m\n                    JOIN message_scopes s ON s.app_id=m.app_id AND s.message_id=m.message_id WHERE s.profile_id=? AND m.created_at>=?', (config.get('assistant_profile_id'), s['active_after']))
                for row in rows:
                    scanned += 1
                    if not _stamp(row.get('created_at')) or row['created_at'] < s['active_after'] or (not _group_allowed(db, row, config)):
                        continue
                    updated += _save(db, s, 'wechat', 'wx_group', row, _wx_group_parts(db, row, config, now), now, name=config['groups'][row['group_id']].get('group_name', '已接入信息群'))
            (wa, wa_db, unavailable) = _wa_safe(s)
            if wa_db:
                rows = _row_batch(wa_db, db, s, 'whatsapp', 'messages', {'channel', 'id', 'profile', 'scope', 'chat', 'business_role', 'direction'}, 'SELECT m.rowid AS _rowid,m.* FROM messages m WHERE m.profile=? AND m.created>=?', (wa.get('profile_id'), s['active_after']))
                for row in rows:
                    scanned += 1
                    if not _stamp(row.get('created')) or row['created'] < s['active_after'] or (not _wa_allowed(row, wa)):
                        continue
                    target = wa.get('groups', {}).get(row['chat'], wa.get('contacts', {}).get(row['chat'], {}))
                    updated += _save(db, s, 'whatsapp', 'whatsapp', row, _wa_parts(wa_db, row), now, name=target.get('group_name', 'WhatsApp客户私聊' if not row['chat'].endswith('@g.us') else 'WhatsApp已接入群'))
        return {'status': 'ready', 'scanned': scanned, 'updated': updated, 'unavailable_sources': unavailable}
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, UnicodeError, OverflowError, RecursionError):
        return {'status': 'config_error' if not locals().get('s') else 'storage_error', 'scanned': 0, 'updated': 0}
    finally:
        if wa_db:
            wa_db.close()

def _visible(store, source, config, s, requester, wa, wa_db):
    if source['company_id'] != s['company_id']:
        return False
    if source['visibility'] == 'personal' and (requester['channel_kind'] != 'private' or source['personal_owner'] != requester['owner_wxid']):
        return False
    row = json.loads(source['origin_json'])
    if source['route'] == 'company_identity':
        return source['platform'] == 'user_confirmed' and s['company_id'] == os.environ.get('EXAMPLE_COMPANY_ID', 'example-company') and (s['company_name'] == os.environ.get('EXAMPLE_COMPANY_NAME', '示例公司'))
    created = row.get('created_at', row.get('created'))
    if not _stamp(created) or created < s['active_after'] or created > requester['created_at']:
        return False
    if source['route'] == 'supplier_directory':
        from supplier_source_sync import visible_source
        return visible_source(store.db, config, s, row, source['source_id'])
    if source['route'] == 'wx_shared_work':
        return _shared_visible(store, config, row, requester)
    if source['route'] == 'wx_owner':
        current = store.db.execute('SELECT * FROM owner_messages WHERE app_id=? AND message_id=?', (row['app_id'], row['message_id'])).fetchone()
        return bool(current and _owner_allowed(dict(current), config, sealed=True) and all((current[k] == row[k] for k in ('owner_wxid', 'profile_id', 'workspace_owner_wxid', 'channel_id', 'channel_kind', 'own_wxid', 'access_kind'))))
    if source['route'] in ('wx_group', 'wx_group_note'):
        current = store.db.execute('SELECT m.*,s.profile_id,s.scope_id,s.own_wxid,s.business_role FROM messages m\n            JOIN message_scopes s ON s.app_id=m.app_id AND s.message_id=m.message_id WHERE m.app_id=? AND m.message_id=?', (row['app_id'], row['message_id'])).fetchone()
        allowed = bool(current and _group_allowed(store.db, dict(current), config, sealed=True) and all((current[k] == row[k] for k in ('group_id', 'profile_id', 'scope_id', 'own_wxid', 'business_role'))))
        if not allowed:
            return False
        if source['route'] == 'wx_group':
            return _group_reference_current(store.db, dict(current), config, time.time())
        from company_note_assets import safe_assets
        children = safe_assets(store.db, row['app_id'], row['message_id'], asset_id=row.get('group_note_asset_id'))
        return bool(len(children) == 1 and all((children[0][key] == row.get(origin) for (key, origin) in (('parent_ordinal', 'group_note_parent_ordinal'), ('ordinal', 'group_note_asset_ordinal'), ('kind', 'group_note_asset_kind'), ('parent_xml_sha256', 'parent_xml_sha256')))))
    if source['route'] == 'whatsapp':
        current = wa_db.execute('SELECT * FROM messages WHERE channel=? AND id=?', (row['channel'], row['id'])).fetchone() if wa_db else None
        return bool(current and _wa_allowed(dict(current), wa) and all((current[k] == row[k] for k in ('profile', 'scope', 'chat', 'business_role', 'direction'))))
    return False

def search(store, config, requester, query, kind=None, limit=5):
    wa_db = None
    try:
        s = _settings(config)
        if s is None:
            return {'status': 'disabled', 'items': []}
        with store.lock:
            if not isinstance(requester, dict) or not _owner_allowed(requester, config, db=store.db):
                return {'status': 'scope_denied', 'items': []}
        if not _stamp(requester.get('created_at')) or not isinstance(query, str) or len(query) > 200 or any((ord(c) < 32 or ord(c) == 127 for c in query)) or (type(limit) is not int) or (not 1 <= limit <= 5) or (kind is not None and (not isinstance(kind, str) or kind not in KINDS)):
            return {'status': 'invalid_arguments', 'items': []}
        (wa, wa_db, unavailable) = _wa_safe(s)
        with store.lock:
            if not _owner_allowed(requester, config, db=store.db):
                return {'status': 'scope_denied', 'items': []}
            if not _table(store.db, 'company_sources', {'source_id', 'latest_version'}):
                return {'status': 'no_records', 'items': []}
            like = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            rows = store.db.execute("SELECT * FROM (SELECT s.*,r.kind,r.text,r.content_status,r.metadata_json,r.ordinal,v.verification_status,\n                row_number() OVER(PARTITION BY s.source_id ORDER BY r.ordinal) AS matching_rank\n                FROM company_sources s JOIN company_versions v ON v.source_id=s.source_id AND v.version_hash=s.latest_version\n                JOIN company_records r ON r.source_id=s.source_id AND r.version_hash=s.latest_version\n                WHERE s.company_id=? AND v.verification_status IN ('candidate','confirmed')\n                AND (s.visibility='shared' OR (? AND s.visibility='personal' AND s.personal_owner=?))\n                AND (r.text LIKE ? ESCAPE '\\' OR s.channel_name LIKE ? ESCAPE '\\') " + (' AND r.kind=? ' if kind else '') + ') WHERE matching_rank=1 ORDER BY created_at DESC,ordinal LIMIT 200', (s['company_id'], requester['channel_kind'] == 'private', requester['owner_wxid'], like, like, *((kind,) if kind else ()))).fetchall()
            items = []
            seen = set()
            for row in rows:
                if row['source_id'] in seen or not _visible(store, row, config, s, requester, wa, wa_db):
                    continue
                if not _group_reference_version_current(store.db, row, row['latest_version'], config, time.time()):
                    continue
                seen.add(row['source_id'])
                meta = json.loads(row['metadata_json'])
                items.append({'id': row['source_id'], 'source_id': row['source_id'], 'kind': row['kind'], 'record_kind': row['kind'], 'text': _clean(row['text'], 4000), 'status': row['verification_status'], 'content_status': row['content_status'], 'unverified': row['verification_status'] != 'confirmed', 'visibility': 'personal' if row['visibility'] == 'personal' else 'internal_only', 'truncated': bool(meta.get('truncated')), 'format': meta.get('format', 'unknown'), 'quote_metadata': {key: value for (key, value) in meta.items() if key not in {'source_provenance', 'source_provenance_index_spans', 'source_provenance_index_start', 'source_provenance_coordinates', 'source_attachment_ordinal', 'indexed_text_start', 'reference_text_sha256', 'reference_revision'}} if row['kind'] == 'quote' else {}, 'source': {'platform': row['platform'], 'channel_name': row['channel_name'], 'created_at_utc': datetime.fromtimestamp(row['created_at'], timezone.utc).isoformat(timespec='seconds'), 'source_hash': row['latest_version']}, 'version_hash': row['latest_version'], 'chunk_ordinal': row['ordinal'], 'can_read_source': True})
                if row['route'] == 'supplier_directory':
                    from supplier_source_sync import source_metadata
                    items[-1]['source'].update(source_metadata(store.db, config, s, json.loads(row['origin_json'])))
                if row['route'] == 'wx_shared_work':
                    first = store.db.execute('SELECT metadata_json FROM company_records WHERE source_id=? AND version_hash=? ORDER BY ordinal LIMIT 1', (row['source_id'], row['latest_version'])).fetchone()
                    items[-1]['source'].update(_shared_metadata(store.db, config, json.loads(row['origin_json']), requester, json.loads(first['metadata_json']) if first else {}))
                    items[-1]['visibility'] = 'shared_work'
                if row['route'] == 'wx_group':
                    items[-1]['source']['forwarded_records'] = _group_provenance_page([row], meta.get('indexed_text_start', 0), len(row['text']))
                items[-1]['source']['indexed_version_count'] = store.db.execute('SELECT count(*) FROM company_versions WHERE source_id=?', (row['source_id'],)).fetchone()[0]
                if len(items) == limit:
                    break
        return {'status': 'ready' if items else 'no_records', 'items': items, 'truncated': len(items) == limit, 'unavailable_sources': unavailable}
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, UnicodeError, OverflowError, RecursionError):
        return {'status': 'config_error' if not locals().get('s') else 'read_failed', 'items': []}
    finally:
        if wa_db:
            wa_db.close()

def read_source(store, config, requester, source_id, offset=0, limit=8000, version_hash=None):
    wa_db = None
    try:
        s = _settings(config)
        if s is None:
            return {'status': 'disabled', 'text': ''}
        if not isinstance(source_id, str) or not _KID.fullmatch(source_id) or type(offset) is not int or (not 0 <= offset <= MAX_SUPPLIER_SOURCE_CHARS + 1000) or (type(limit) is not int) or (not 1 <= limit <= 8000) or (version_hash is not None and (not isinstance(version_hash, str) or not re.fullmatch('[a-f0-9]{64}', version_hash))):
            return {'status': 'invalid_arguments', 'text': ''}
        with store.lock:
            if not isinstance(requester, dict) or not _owner_allowed(requester, config, db=store.db):
                return {'status': 'scope_denied', 'text': ''}
        (wa, wa_db, _) = _wa_safe(s)
        with store.lock:
            if not _owner_allowed(requester, config, db=store.db):
                return {'status': 'scope_denied', 'text': ''}
            source = store.db.execute('SELECT * FROM company_sources WHERE source_id=?', (source_id,)).fetchone()
            if source is None or not _visible(store, source, config, s, requester, wa, wa_db):
                return {'status': 'scope_denied', 'text': ''}
            version_hash = version_hash or source['latest_version']
            version = store.db.execute('SELECT verification_status FROM company_versions WHERE source_id=? AND version_hash=?', (source_id, version_hash)).fetchone()
            if version is None or version[0] not in ('candidate', 'confirmed'):
                return {'status': 'unavailable_version', 'text': ''}
            records = store.db.execute('SELECT * FROM company_records WHERE source_id=? AND version_hash=? ORDER BY ordinal', (source_id, version_hash)).fetchall()
            if not _group_reference_version_current(store.db, source, version_hash, config, time.time(), records):
                return {'status': 'scope_denied', 'text': ''}
            version_count = store.db.execute('SELECT count(*) FROM company_versions WHERE source_id=?', (source_id,)).fetchone()[0]
            versions = [{'version_hash': v['version_hash'], 'verification_status': v['verification_status'], 'indexed_at_utc': datetime.fromtimestamp(v['created_at'], timezone.utc).isoformat(timespec='seconds')} for v in store.db.execute('SELECT * FROM company_versions WHERE source_id=? ORDER BY created_at DESC,version_hash LIMIT 32', (source_id,))]
            indexed_text = ''.join((row['text'] for row in records))
            text = _clean(indexed_text, MAX_SUPPLIER_SOURCE_CHARS, strip=False)
            group_coordinates_valid = source['route'] != 'wx_group' or text == indexed_text
            total = len(text)
            end = min(offset + limit, total)
            metadata = {'platform': source['platform'], 'channel_name': source['channel_name'], 'created_at_utc': datetime.fromtimestamp(source['created_at'], timezone.utc).isoformat(timespec='seconds')}
            if source['route'] == 'supplier_directory':
                from supplier_source_sync import source_metadata
                metadata.update(source_metadata(store.db, config, s, json.loads(source['origin_json'])))
                if records:
                    version_metadata = json.loads(records[0]['metadata_json'])
                    metadata['file_sha256'] = version_metadata.get('file_sha256', '')
                    metadata['indexed_modified_at_utc'] = datetime.fromtimestamp(version_metadata['source_modified_at'], timezone.utc).isoformat(timespec='seconds') if version_metadata.get('source_modified_at') else None
                if version_hash != source['latest_version']:
                    metadata['freshness'] = 'historical_indexed_version'
            if source['route'] == 'wx_shared_work':
                metadata.update(_shared_metadata(store.db, config, json.loads(source['origin_json']), requester, json.loads(records[0]['metadata_json']) if records else {}))
                if version_hash != source['latest_version']:
                    metadata['freshness'] = 'historical_indexed_version'
            if source['route'] == 'wx_group':
                metadata['forwarded_records'] = _group_provenance_page(records, offset, max(0, end - offset)) if group_coordinates_valid else []
                references = []
                seen = set()
                for record in records:
                    meta = json.loads(record['metadata_json'])
                    position = meta.get('indexed_text_start')
                    if type(position) is not int or position >= end or position + len(record['text']) <= offset:
                        continue
                    reference = _safe_reference_provenance(meta.get('reference_provenance'))
                    key = _json(reference)
                    if reference and key not in seen:
                        references.append(reference)
                        seen.add(key)
                if references:
                    metadata['referenced_media'] = references[:8]
                if not group_coordinates_valid:
                    metadata['forward_structure_status'] = 'index_text_cleaning_changed_coordinates'
            return {'status': 'ready', 'source_id': source_id, 'version_hash': version_hash, 'text': text[offset:end], 'offset': offset, 'next_offset': end if end < total else None, 'total_chars': total, 'has_more': end < total, 'truncated': not group_coordinates_valid or any((json.loads(row['metadata_json']).get('truncated') for row in records)), 'verification_status': version[0], 'visibility': 'shared_work' if source['route'] == 'wx_shared_work' else 'personal' if source['visibility'] == 'personal' else 'internal_only', 'content_statuses': sorted({row['content_status'] for row in records}), 'source': metadata, 'is_latest_indexed_version': version_hash == source['latest_version'], 'indexed_versions': versions, 'indexed_version_count': version_count, 'version_list_truncated': version_count > len(versions), 'verification_scope': 'indexed_version_only'}
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, UnicodeError, OverflowError, RecursionError):
        return {'status': 'read_failed', 'text': ''}
    finally:
        if wa_db:
            wa_db.close()

def review(store, config, requester, source_id, action, now=None):
    wa_db = None
    try:
        s = _settings(config)
        if s is None:
            return {'status': 'disabled'}
        with store.lock:
            if not isinstance(requester, dict) or not _owner_allowed(requester, config, db=store.db):
                return {'status': 'scope_denied'}
        now = time.time() if now is None else now
        if not _stamp(now) or not _stamp(requester.get('created_at')) or (not isinstance(source_id, str)) or (not _KID.fullmatch(source_id)) or (not isinstance(action, str)) or (action not in ('confirmed', 'rejected', 'expired')):
            return {'status': 'invalid_arguments'}
        (wa, wa_db, _) = _wa_safe(s)
        with store.lock, store.db:
            if not _owner_allowed(requester, config, db=store.db):
                return {'status': 'scope_denied'}
            source = store.db.execute('SELECT * FROM company_sources WHERE source_id=?', (source_id,)).fetchone()
            if source is None or not _visible(store, source, config, s, requester, wa, wa_db):
                return {'status': 'scope_denied'}
            if source['route'] == 'wx_shared_work' and json.loads(source['origin_json']).get('owner_wxid') != requester['owner_wxid']:
                return {'status': 'scope_denied'}
            if action == 'confirmed' and store.db.execute("SELECT 1 FROM company_records WHERE source_id=? AND version_hash=? AND content_status!='ready'", (source_id, source['latest_version'])).fetchone():
                return {'status': 'material_not_ready'}
            store.db.execute('INSERT INTO company_reviews(company_id,source_id,version_hash,action,reviewer_id,reviewed_at,origin_json) VALUES(?,?,?,?,?,?,?)', (s['company_id'], source_id, source['latest_version'], action, requester['owner_wxid'], now, source['origin_json']))
            store.db.execute('UPDATE company_versions SET verification_status=? WHERE source_id=? AND version_hash=?', (action, source_id, source['latest_version']))
        return {'status': 'reviewed', 'action': action, 'source_id': source_id, 'visibility': 'shared_work' if source['route'] == 'wx_shared_work' else 'personal' if source['visibility'] == 'personal' else 'internal_only'}
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, UnicodeError, OverflowError, RecursionError):
        return {'status': 'config_error' if not locals().get('s') else 'review_failed'}
    finally:
        if wa_db:
            wa_db.close()
_REPORT_ROUTES = ('wx_group', 'wx_group_note', 'wx_shared_work')

def _report_requester(store, config, recipient, cutoff):
    owner = config.get('owner_control', {})
    operators = config.get('group_onboarding', {}).get('operator_ids', [])
    from owner_work_sharing import settings as sharing_settings
    sharing = sharing_settings(config)
    if not _stamp(cutoff) or not isinstance(operators, list) or len(operators) != 2 or (len(set(operators)) != 2) or (recipient not in operators) or (not sharing) or (set(sharing['member_ids']) != set(operators)):
        return None
    watermark = store.db.execute('SELECT coalesce(max(rowid),0) FROM owner_messages WHERE created_at<=?', (cutoff,)).fetchone()[0]
    context = dict(app_id=config.get('app_id'), own_wxid=owner.get('own_wxid'), owner_wxid=recipient, profile_id=config.get('assistant_profile_id'), workspace_owner_wxid=owner.get('owner_wxid'), channel_kind='private', channel_id=recipient, access_kind='work', created_at=cutoff, rowid=watermark)
    return context if _owner_allowed(context, config, db=store.db) else None

def _report_source_visible(store, config, source, requester):
    if source['route'] not in _REPORT_ROUTES or source['visibility'] != 'shared' or source['personal_owner'] or source['needs_refresh']:
        return False
    origin = json.loads(source['origin_json'])
    if source['route'] in ('wx_group', 'wx_group_note'):
        from collection_policy import group_view
        live = group_view(config, origin.get('group_id'), db=store.db, app_id=origin.get('app_id'), own_wxid=origin.get('own_wxid'), created_at=requester['created_at'])
        if live is None or live.get('business_role') not in ('supplier', 'customer', 'internal'):
            return False
    return _visible(store, source, config, _settings(config), requester, None, None)

def report_sources(store, config, recipient, cutoff, start, limit=200):
    try:
        if not _stamp(start) or start > cutoff or type(limit) is not int or (not 1 <= limit <= 200):
            return {'status': 'invalid_arguments', 'sources': []}
        with store.lock:
            requester = _report_requester(store, config, recipient, cutoff)
            settings = _settings(config)
            if requester is None or settings is None:
                return {'status': 'scope_denied', 'sources': []}
            if not _table(store.db, 'company_sources', {'source_id', 'latest_version'}):
                return {'status': 'ready', 'sources': [], 'omitted_sources': 0}
            rows = store.db.execute("SELECT s.*,v.created_at AS indexed_at FROM company_sources s\n                JOIN company_versions v ON v.source_id=s.source_id AND v.version_hash=s.latest_version\n                WHERE s.company_id=? AND s.platform='wechat' AND s.visibility='shared'\n                  AND s.personal_owner='' AND s.route IN ('wx_group','wx_group_note','wx_shared_work')\n                  AND s.created_at<=? AND v.created_at<=?\n                  AND (s.created_at>=? OR v.created_at>=?)\n                  AND v.verification_status IN ('candidate','confirmed')\n                ORDER BY max(s.created_at,v.created_at) DESC,s.source_id LIMIT 1001", (settings['company_id'], cutoff, cutoff, start, start)).fetchall()
            visible = []
            for source in rows:
                if not _report_source_visible(store, config, source, requester):
                    continue
                origin = json.loads(source['origin_json'])
                versions = [dict(row) for row in store.db.execute("SELECT version_hash,verification_status,created_at\n                    FROM company_versions WHERE source_id=? AND created_at<=?\n                    AND verification_status IN ('candidate','confirmed')\n                    ORDER BY created_at DESC,version_hash LIMIT 3", (source['source_id'], cutoff))]
                if source['route'] == 'wx_shared_work':
                    versions = [v for v in versions if v['version_hash'] == source['latest_version']]
                visible.append(dict(source_id=source['source_id'], version_hash=source['latest_version'], route=source['route'], received_epoch=source['created_at'], indexed_epoch=source['indexed_at'], source_role=origin.get('business_role', 'unknown'), channel_label=_clean(source['channel_name'], 100), versions=versions))
            return {'status': 'ready', 'sources': visible[:limit], 'omitted_sources': max(0, len(visible) - limit), 'enumeration_capped': len(rows) == 1001}
    except (ValueError, TypeError, KeyError, sqlite3.Error, UnicodeError, OverflowError):
        return {'status': 'read_failed', 'sources': []}

def report_source_page(store, config, recipient, source_id, version_hash, offset=0, limit=8000, *, cutoff):
    try:
        with store.lock:
            requester = _report_requester(store, config, recipient, cutoff)
            if requester is None:
                return {'status': 'scope_denied', 'text': ''}
            source = store.db.execute('SELECT * FROM company_sources WHERE source_id=?', (source_id,)).fetchone()
            if source is None or not _report_source_visible(store, config, source, requester):
                return {'status': 'scope_denied', 'text': ''}
            if source['route'] == 'wx_shared_work' and version_hash != source['latest_version']:
                return {'status': 'unavailable_version', 'text': ''}
            version = store.db.execute('SELECT created_at FROM company_versions WHERE source_id=? AND version_hash=?', (source_id, version_hash)).fetchone()
            if version is None or version[0] > cutoff:
                return {'status': 'unavailable_version', 'text': ''}
            records = store.db.execute('SELECT kind,text,content_status,metadata_json FROM company_records WHERE source_id=? AND version_hash=? ORDER BY ordinal', (source_id, version_hash)).fetchall()
            witness = [(r['kind'], r['text'], r['content_status'], json.loads(r['metadata_json'])) for r in records]
            if hashlib.sha256(_json(witness).encode()).hexdigest() != version_hash:
                return {'status': 'version_integrity_failed', 'text': ''}
            isolated = dict(config, company_knowledge=dict(config['company_knowledge']))
            isolated['company_knowledge'].pop('whatsapp_config', None)
            result = read_source(store, isolated, requester, source_id, offset, limit, version_hash)
            if result.get('status') == 'ready':
                result['report_current_version'] = source['latest_version']
                latest = store.db.execute('SELECT created_at FROM company_versions WHERE source_id=? AND version_hash=?', (source_id, source['latest_version'])).fetchone()
                origin = json.loads(source['origin_json'])
                result['report_source_metadata'] = {'route': source['route'], 'received_epoch': source['created_at'], 'indexed_epoch': latest[0], 'source_role': origin.get('business_role', 'unknown')}
                result['report_source_limits'] = _report_source_limits(store.db, source, origin)
            return result
    except (ValueError, TypeError, KeyError, sqlite3.Error, UnicodeError, OverflowError):
        return {'status': 'read_failed', 'text': ''}

def report_source_status(store, config, recipient, source_id, version_hash, *, cutoff):
    try:
        with store.lock:
            requester = _report_requester(store, config, recipient, cutoff)
            source = store.db.execute('SELECT * FROM company_sources WHERE source_id=?', (source_id,)).fetchone()
            if requester is None or source is None or (not _report_source_visible(store, config, source, requester)):
                return {'status': 'scope_denied'}
            if source['route'] == 'wx_shared_work' and version_hash != source['latest_version']:
                return {'status': 'unavailable_version'}
            version = store.db.execute('SELECT created_at,verification_status FROM company_versions WHERE source_id=? AND version_hash=?', (source_id, version_hash)).fetchone()
            latest = store.db.execute('SELECT created_at FROM company_versions WHERE source_id=? AND version_hash=?', (source_id, source['latest_version'])).fetchone()
            if version is None or latest is None or version[0] > cutoff or (latest[0] > cutoff):
                return {'status': 'unavailable_version'}
            origin = json.loads(source['origin_json'])
            return {'status': 'ready', 'current_version_hash': source['latest_version'], 'route': source['route'], 'received_epoch': source['created_at'], 'indexed_epoch': latest[0], 'source_role': origin.get('business_role', 'unknown'), 'verification_status': version['verification_status'], 'source_limits': _report_source_limits(store.db, source, origin)}
    except (ValueError, TypeError, KeyError, sqlite3.Error, UnicodeError, OverflowError):
        return {'status': 'read_failed'}

def _report_source_limits(db, source, origin):
    candidates = []
    if source['route'] == 'wx_group' and _table(db, 'company_media', {'app_id', 'message_id', 'archive_bytes', 'status'}):
        candidates = [dict(r) for r in db.execute('SELECT archive_bytes,status FROM company_media WHERE app_id=? AND message_id=?', (origin.get('app_id'), origin.get('message_id')))]
    elif source['route'] == 'wx_group_note' and _table(db, 'company_note_assets', {'asset_id', 'archive_bytes', 'status'}):
        candidates = [dict(r) for r in db.execute('SELECT archive_bytes,status FROM company_note_assets WHERE asset_id=?', (origin.get('group_note_asset_id'),))]
    elif source['route'] == 'wx_shared_work':
        (table, key, value) = ('owner_note_assets', 'asset_id', origin.get('note_asset_id')) if origin.get('note_asset_id') else ('owner_materials', 'material_id', origin.get('material_id'))
        if _table(db, table, {key, 'archive_bytes', 'status'}):
            candidates = [dict(r) for r in db.execute('SELECT archive_bytes,status FROM ' + table + ' WHERE ' + key + '=?', (value,))]
    limits = []
    if any((type(r.get('archive_bytes')) is int and r['archive_bytes'] > 20 * 1024 * 1024 or r.get('status') in ('large_extracting', 'large_ready') for r in candidates)):
        limits.append('large_original_not_in_content_analysis')
    if any((r.get('status') in ('original_saved_unparsed', 'pending', 'processing', 'downloaded', 'archived') for r in candidates)):
        limits.append('original_saved_or_pending_unparsed')
    return limits
