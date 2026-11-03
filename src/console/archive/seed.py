"""Offline, read-only source imports; dry runs stay entirely in memory."""
from collections import Counter, defaultdict
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import unicodedata

from .store import ARCHIVE_PATH, Store, configure, migrate, insert, update, now
from .service import source
from . import validation as v

CATALOG_PATH = Path('/var/lib/imgw/video_knowledge_auto/equipment/catalog.sqlite3')
REPORT_PATH = Path('/var/lib/imgw/supplier-packing/packing_report.json')
MAX_REPORT_BYTES = 64 * 1024 * 1024
SHORT_NAME_RULES = (
    (r'\([^)]*\)', ''),
    (r'^(?:广东省?|广州市?|深圳市?|佛山市?|东莞市?)', ''),
    (r'(?:有限责任公司|股份有限公司|有限公司|公司)$', ''),
    (r'\s+(?:舞台灯光|包房灯|舞台灯|灯光)$', ''),
    (r'(?:舞台灯光设备|舞台灯光|灯光设备|光电科技|照明科技|照明设备|灯光|灯饰|电子科技|电器|设备)(?:厂)?$', ''),
)
LABEL_REJECTIONS = (
    r'^\d+(?:[.、:：)）]|\s)', r'^[一二三四五六七八九十]+[、.：:]',
    r'^(?:说明|备注|注意|产品参数|技术参数|光源|电压|功率|尺寸|外箱|毛重|净重|重量|包装|采用|本产品|该产品|支持|具有|内置|配备)',
    r'[=|\n\r；;。！？!?：:…]', r'_xlfn|DISPIMG',
)
MODEL_PATTERN = re.compile(r'(?<![A-Za-z0-9])(?:[A-Za-z]{1,12}[-_ ]?\d{1,8}[A-Za-z0-9_-]*|\d{1,6}[A-Za-z]{1,8}[A-Za-z0-9_-]*)(?![A-Za-z0-9])')
FACT_MAP = {'carton_size': ('carton_l_cm', 'carton_w_cm', 'carton_h_cm'),
            'product_size': ('product_l_cm', 'product_w_cm', 'product_h_cm'),
            'gross_weight_kg': ('gross_kg',), 'net_weight_kg': ('net_kg',),
            'units_per_carton': ('units_per_carton',)}


def short_name(name):
    value = unicodedata.normalize('NFKC', name).strip()
    for pattern, replacement in SHORT_NAME_RULES:
        value = re.sub(pattern, replacement, value).strip()
    return value or name.strip()


def is_model_label(label):
    if not isinstance(label, str):
        return False
    if any(unicodedata.category(c).startswith('C') for c in label):
        return False
    value = unicodedata.normalize('NFKC', label).strip()
    return (2 <= len(value) <= 60 and not any(re.search(p, value, re.I) for p in LABEL_REJECTIONS)
            and any(not re.fullmatch(r'\d+(?:W|KW|V|HZ|KG|CM|MM)', m.group(), re.I)
                    for m in MODEL_PATTERN.finditer(value)))


def read_report(path):
    with Path(path).open('rb') as handle:
        before = os.fstat(handle.fileno())
        if before.st_size > MAX_REPORT_BYTES:
            raise ValueError('包装报告过大')
        data = handle.read(MAX_REPORT_BYTES + 1)
        after = os.fstat(handle.fileno())
    if len(data) > MAX_REPORT_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('包装报告读取失败')
    def reject(_):
        raise ValueError('包装报告数值无效')
    report = json.loads(data, parse_constant=reject)
    if (not isinstance(report, dict) or report.get('schema_version') != 2
            or not isinstance(report.get('products'), list) or not isinstance(report.get('supplier_coverage'), list)):
        raise ValueError('包装报告结构无效')
    seen = set()
    for row in report['products']:
        if not all(isinstance(row.get(k), str) for k in ('equipment_id', 'supplier', 'label', 'source_file')):
            raise ValueError('包装条目结构无效')
        if not row['equipment_id'] or row['equipment_id'] in seen:
            raise ValueError('包装条目标识重复或缺失')
        seen.add(row['equipment_id'])
        if not isinstance(row.get('facts'), dict) or not isinstance(row.get('completeness'), dict):
            raise ValueError('包装事实结构无效')
        for kind, group in row['facts'].items():
            if (group.get('kind') != kind or type(group.get('conflict')) is not bool
                    or not isinstance(group.get('candidates'), list) or not group['candidates']):
                raise ValueError('包装候选结构无效')
            for candidate in group['candidates']:
                value = candidate['value']
                numbers = value if isinstance(value, list) else [value]
                if not all(type(n) in (float, int) and math.isfinite(n) and n > 0 for n in numbers):
                    raise ValueError('包装数值无效')
                if kind.endswith('_size') and (not isinstance(value, list) or len(value) != 3):
                    raise ValueError('包装尺寸无效')
    for row in report['supplier_coverage']:
        v.text(row['supplier'], 500, True)
    return report


def read_catalog(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        configure(db)
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        queries = {
            'suppliers': 'SELECT supplier_id,legal_name FROM cat_supplier ORDER BY supplier_id',
            'models': 'SELECT supplier_model_id,supplier_id,factory_model_code FROM cat_supplier_model ORDER BY supplier_model_id',
            'offers': '''SELECT offer_id,supplier_id,supplier_model_id,quoted_on,valid_until,raw_cost_rmb_fen,
                 tax_basis,includes_flightcase,moq_units,lead_days_min,lead_days_max,corrects_offer_id
                 FROM cat_supplier_offer ORDER BY offer_id''',
            'specs': '''SELECT e.equipment_id,e.supplier,e.model,e.fixture_type,e.wattage,e.asset_id,a.filename
                FROM equipment_specs e LEFT JOIN multimedia_assets a ON e.asset_id=a.asset_id
                WHERE e.model IS NOT NULL AND trim(e.model) != '' ORDER BY e.equipment_id''',
            'registry': '''SELECT channel_kind,source_scope,display_name,channel_role,observed_at
                FROM channel_role_registry ORDER BY observed_at DESC''',
        }
        result = {k: [dict(r) for r in db.execute(q)] for k, q in queries.items()}
    if len({r['equipment_id'] for r in result['specs']}) != len(result['specs']):
        raise ValueError('来源关联不唯一')
    return result


def safe_target(target, *inputs):
    target = Path(target)
    for path in map(Path, inputs):
        if target.resolve() == path.resolve() or (target.exists() and path.exists() and os.path.samefile(target, path)):
            raise ValueError('来源与档案不能使用同一文件')


def memory_store(path):
    db = sqlite3.connect(':memory:')
    try:
        configure(db)
        if Path(path).exists():
            # Opening a checkpointed WAL database with mode=ro alone can CREATE
            # empty -wal/-shm files. Immutable reads avoid that for a closed DB;
            # never use immutable when there are committed WAL frames to read.
            wal, shm = Path(str(path) + '-wal'), Path(str(path) + '-shm')
            has_wal = wal.exists() and wal.stat().st_size > 0
            if has_wal and not shm.exists():
                raise ValueError('档案正在切换日志状态，请稍后重试')
            before = Path(path).stat()
            uri = Path(path).resolve().as_uri() + '?mode=ro' + ('' if has_wal else '&immutable=1')
            with closing(sqlite3.connect(uri, uri=True)) as original:
                original.execute('PRAGMA query_only=ON')
                original.backup(db)
            after = Path(path).stat()
            if not has_wal and ((before.st_mtime_ns, before.st_size, before.st_ino) !=
                                (after.st_mtime_ns, after.st_size, after.st_ino)
                                or (wal.exists() and wal.stat().st_size > 0)):
                raise ValueError('档案正在变化，请稍后重试')
        migrate(db)
        return Store(path, connection=db)
    except BaseException:
        db.close()
        raise


def _stats():
    return dict(created=Counter(), updated=Counter(), skipped=Counter(), conversation_matches=0)


def _result(stats, dry_run):
    for group in ('created', 'updated'):
        for kind in ('suppliers', 'products', 'offers', 'sources'):
            stats[group].setdefault(kind, 0)
    return dict(dry_run=dry_run, **{k: dict(val) if isinstance(val, Counter) else val for k, val in stats.items()})


def _source(db, stats, pid, kind, ref, title, who, note=None):
    old = db.execute('SELECT id,note FROM product_sources WHERE product_id=? AND kind=? AND ref=?', (pid, kind, ref)).fetchone()
    source(db, pid, kind, ref, title, who, note)
    if old is None:
        stats['created']['sources'] += 1
    elif note and note != old['note']:
        stats['updated']['sources'] += 1


def _product(db, stats, supplier_id, model, kind, ref, title, who, extra=None):
    model = v.text(model, v.PRODUCT_TEXT['model'], True)
    # Stable source links survive renames and merges; never recreate merged rows.
    existing = db.execute('SELECT p.* FROM products p JOIN product_sources s ON s.product_id=p.id '
                          'WHERE s.kind=? AND s.ref=? ORDER BY p.id LIMIT 1', (kind, ref)).fetchone()
    if existing is None:
        key = v.normalize_model(model)
        existing = next((p for p in db.execute('SELECT * FROM products WHERE supplier_id IS ? ORDER BY id', (supplier_id,))
                         if v.normalize_model(p['model'] or p['name']) == key), None)
    if existing:
        pid = existing['id']
        stats['skipped']['product_existing'] += 1
    else:
        fields = dict(supplier_id=supplier_id, name=model, model=model, **(extra or {}))
        stamp = now()
        state = {f: dict(state='extracted', source=ref) for f, val in fields.items() if val is not None}
        pid = insert(db, 'products', dict(fields, status='candidate', field_state_json=json.dumps(state),
                                          created_at=stamp, updated_at=stamp), who, 'seed')
        stats['created']['products'] += 1
    _source(db, stats, pid, kind, ref, title, who)
    return pid


def _packing(db, groups, stats, who, *, sync_only=False):
    for pid, entries in groups.items():
        old = dict(db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone())
        state = json.loads(old['field_state_json'])
        candidates = defaultdict(list)
        conflict = any(e['completeness'].get('status') == 'conflict' or any(g['conflict'] for g in e['facts'].values()) for e in entries)
        for entry in entries:
            for kind, fields in FACT_MAP.items():
                group = entry['facts'].get(kind)
                if not group:
                    continue
                # All candidates participate in cross-document conflict checks.
                for candidate in group['candidates']:
                    value = candidate['value']
                    vals = value if len(fields) == 3 else [value]
                    for field, val in zip(fields, vals):
                        try:
                            if field == 'units_per_carton' and type(val) is not int:
                                raise v.BadRequest('装箱数无效')
                            parsed = v.product_fields({field: str(val)})[field]
                        except v.BadRequest:
                            stats['skipped']['packing_out_of_range'] += 1
                            conflict = True
                            continue
                        candidates[field].append((parsed, entry['equipment_id']))
        for field, vals in candidates.items():
            numbers = [n for n, _ in vals]
            if max(numbers) - min(numbers) > min(numbers) * 0.02 + 1e-9:
                conflict = True
            # Do not choose between different source documents across imports.
            previous = state.get(field, {})
            if (old[field] is not None and previous.get('state') == 'extracted'
                    and previous.get('source') not in {ref for _, ref in vals}
                    and abs(old[field] - numbers[0]) > min(old[field], numbers[0]) * 0.02 + 1e-9):
                conflict = True
        values = {}
        if conflict:
            stats['skipped']['packing_conflict'] += len(entries)
            for entry in entries:
                _source(db, stats, pid, 'equipment_spec', entry['equipment_id'], '包装资料', who, '包装资料有冲突')
            # Withdraw only previously extracted facts; never retain a stale
            # first candidate as a usable package after a conflict appears.
            for field in v.PACKING_FIELDS:
                previous = state.get(field, {})
                if previous.get('state') in ('manual', 'confirmed'):
                    continue
                if previous.get('state') == 'extracted' and old[field] is not None:
                    values[field] = None
                    state[field] = {**state[field], 'conflict': True}
                elif field in candidates and not sync_only and not previous:
                    values[field] = None
                    state[field] = dict(state='extracted', source=candidates[field][0][1], conflict=True)
        else:
            for field, vals in candidates.items():
                previous = state.get(field, {})
                if previous.get('state') in ('manual', 'confirmed') or (
                        sync_only and previous.get('state') != 'extracted'):
                    stats['skipped']['packing_protected_field'] += 1
                    continue
                val, ref = vals[0]
                if old[field] != val or previous.get('source') != ref or previous.get('conflict'):
                    values[field] = val
                    state[field] = dict(state='extracted', source=ref)
        if values:
            update(db, 'products', pid, dict(values, field_state_json=json.dumps(state), updated_at=now()), who, 'sync_packing')
            stats['updated']['products'] += 1
        elif not conflict:
            stats['skipped']['packing_unchanged'] += 1


def seed_archive(catalog_path=CATALOG_PATH, report_path=REPORT_PATH, archive_path=ARCHIVE_PATH, *, dry_run=False, who='seed_archive'):
    safe_target(archive_path, catalog_path, report_path)
    catalog, report = read_catalog(catalog_path), read_report(report_path)
    stats = _stats()
    store = memory_store(archive_path) if dry_run else Store(archive_path)
    try:
        with store.write() as db:
            names = {r['supplier'] for r in report['supplier_coverage']} | {r['legal_name'] for r in catalog['suppliers']}
            supplier_ids = {}
            registry = {}
            for r in catalog['registry']:
                registry.setdefault(r['source_scope'], r)
            for name in sorted(names):
                if not name.strip() or name.strip() == '未注明供应商':
                    stats['skipped']['unknown_supplier'] += 1
                    continue
                v.text(name, 500, True)
                short = short_name(name)
                matches = {r['source_scope'] for r in registry.values()
                           if r['channel_role'] == 'CHANNEL_ROLE_SUPPLIER' and r['channel_kind'] == 'group'
                           and min(len(short), len(short_name(r['display_name'] or ''))) >= 2
                           and (short in short_name(r['display_name']) or short_name(r['display_name']) in short)}
                ref = None
                if len(matches) == 1:
                    stats['conversation_matches'] += 1
                    ref = json.dumps(dict(channel='wechat', scope=next(iter(matches))), ensure_ascii=False, separators=(',', ':'))
                elif matches:
                    stats['skipped']['conversation_ambiguous'] += 1
                existing = db.execute("SELECT id FROM parties WHERE kind='supplier' AND name=?", (name,)).fetchone()
                if existing:
                    supplier_ids[name] = existing['id']
                    stats['skipped']['supplier_existing'] += 1
                else:
                    stamp = now()
                    supplier_ids[name] = insert(db, 'parties', dict(kind='supplier', name=name, short_name=short,
                        aliases_json=json.dumps([name], ensure_ascii=False), conversation_ref=ref,
                        created_at=stamp, updated_at=stamp), who, 'seed')
                    stats['created']['suppliers'] += 1
            legacy_suppliers = {r['supplier_id']: supplier_ids.get(r['legal_name']) for r in catalog['suppliers']}
            models = {}
            for r in catalog['models']:
                supplier = legacy_suppliers.get(r['supplier_id'])
                if supplier is None:
                    stats['skipped']['model_missing_supplier'] += 1
                    continue
                try:
                    models[r['supplier_model_id']] = _product(db, stats, supplier, r['factory_model_code'], 'offer',
                        f"cat_supplier_model:{r['supplier_model_id']}", '结构化型号目录', who)
                except v.BadRequest:
                    stats['skipped']['model_invalid'] += 1
            offer_ids, corrections = {}, []
            for r in catalog['offers']:
                ref = f"cat_supplier_offer:{r['offer_id']}"
                old = db.execute('SELECT id FROM offers WHERE source_ref=? ORDER BY id LIMIT 1', (ref,)).fetchone()
                if old:
                    offer_ids[r['offer_id']] = old['id']
                    stats['skipped']['offer_existing'] += 1
                    continue
                pid = models.get(r['supplier_model_id'])
                if pid is None:
                    stats['skipped']['offer_missing_product'] += 1
                    continue
                try:
                    fields = dict(price=v.fen_to_money(r['raw_cost_rmb_fen']), currency='CNY', unit='未注明',
                                  quoted_on=r['quoted_on'], valid_until=r['valid_until'] or '', source_ref=ref)
                    for key in ('tax_basis', 'includes_flightcase', 'moq_units', 'lead_days_min', 'lead_days_max'):
                        if r[key] is not None:
                            fields[key] = str(r[key])
                    values = v.quote_fields(fields)
                except v.BadRequest:
                    stats['skipped']['offer_invalid'] += 1
                    continue
                stamp = now()
                oid = insert(db, 'offers', dict(values, product_id=pid, status='suggested', created_at=stamp, updated_at=stamp), who, 'seed')
                offer_ids[r['offer_id']] = oid
                corrections.append((oid, r['corrects_offer_id'], pid))
                stats['created']['offers'] += 1
                _source(db, stats, pid, 'offer', ref, '结构化报价记录', who)
            from .service import Archive
            for oid, correction, pid in corrections:
                if correction is not None:
                    if correction not in offer_ids:
                        raise ValueError('报价更正引用缺失')
                    values = dict(product_id=pid, corrects_offer_id=offer_ids[correction])
                    Archive._correction(db, values, oid)
                    update(db, 'offers', oid, {'corrects_offer_id': offer_ids[correction]}, who, 'seed_correction')
                    stats['updated']['offers'] += 1
            for r in catalog['specs']:
                try:
                    extra = {}
                    if r['fixture_type']:
                        extra['category'] = v.text(r['fixture_type'], 100)
                    if r['wattage'] is not None:
                        try:
                            extra['wattage'] = v.number(str(r['wattage']), 0.01, 100000)
                        except v.BadRequest:
                            stats['skipped']['wattage_invalid'] += 1
                    pid = _product(db, stats, supplier_ids.get(r['supplier']), r['model'], 'equipment_spec',
                                   r['equipment_id'], '规格条目', who, extra)
                    if r['asset_id']:
                        title = Path((r['filename'] or '').replace('\\', '/')).name[:200] or '来源文件'
                        _source(db, stats, pid, 'asset', r['asset_id'], title, who)
                except v.BadRequest:
                    stats['skipped']['spec_invalid'] += 1
            groups = defaultdict(list)
            for r in report['products']:
                if not any(r['facts'].get(k) for k in ('carton_size', 'gross_weight_kg')):
                    stats['skipped']['packing_no_carton_or_gross'] += 1
                    continue
                if not is_model_label(r['label']):
                    stats['skipped']['packing_fragment_label'] += 1
                    continue
                pid = _product(db, stats, supplier_ids.get(r['supplier']), r['label'], 'equipment_spec',
                               r['equipment_id'], '包装资料', who)
                groups[pid].append(r)
                if r.get('asset_id'):
                    _source(db, stats, pid, 'asset', r['asset_id'], Path(r['source_file'].replace('\\', '/')).name[:200] or '来源文件', who)
            _packing(db, groups, stats, who)
        return _result(stats, dry_run)
    finally:
        if dry_run:
            store.connection.close()


def sync_packing_facts(report_path=REPORT_PATH, archive_path=ARCHIVE_PATH, *, dry_run=False, who='sync_packing'):
    safe_target(archive_path, report_path)
    report = read_report(report_path)
    stats = _stats()
    store = memory_store(archive_path) if dry_run else Store(archive_path)
    try:
        with store.write() as db:
            groups = defaultdict(list)
            for entry in report['products']:
                matches = db.execute("SELECT DISTINCT product_id FROM product_sources WHERE kind='equipment_spec' AND ref=?", (entry['equipment_id'],)).fetchall()
                if len(matches) != 1:
                    stats['skipped']['packing_unlinked'] += 1
                    continue
                groups[matches[0]['product_id']].append(entry)
            _packing(db, groups, stats, who, sync_only=True)
        return _result(stats, dry_run)
    finally:
        if dry_run:
            store.connection.close()
