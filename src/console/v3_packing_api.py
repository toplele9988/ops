"""Packing report JSON behind the console's two authentication gates.

The console middleware checks CSRF and may pre-read the POST form. A contacted
entry only records a human's action: this module never sends supplier messages.
"""
from collections import Counter
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import parse_qs

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

REPORT_PATH = Path('/var/lib/imgw/supplier-packing/packing_report.json')
STATE_PATH = Path('/var/lib/imgw/ops-app/actions.sqlite3')
MAX_BYTES = 64 * 1024 * 1024
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
STATUS_TEXT = {
    'ready': '可直接报价（资料齐全）',
    'estimable': '可估算（须向供应商确认装箱数）',
    'partial': '资料不全', 'missing': '暂无包装资料', 'conflict': '资料有冲突',
}
CONFLICT_TEXT = {
    'multi_model_suspected': '疑似一条资料混入多个型号，需拆分后再核对',
    'value_mismatch': '同一项数值不一致，需向供应商确认',
}
FACT_FIELDS = {'carton_cm': 'carton_size', 'gross_kg': 'gross_weight_kg',
               'net_kg': 'net_weight_kg', 'units_per_carton': 'units_per_carton',
               'product_cm': 'product_size'}
FACT_KINDS = set(FACT_FIELDS.values()) | {'flightcase_size', 'flightcase_weight_kg'}
STATUS_ORDER = {status: index for index, status in enumerate(
    ('conflict', 'estimable', 'partial', 'missing', 'ready'))}
_STATE_LOCK = threading.Lock()


class _BadRequest(ValueError):
    """Only fixed, user-facing Chinese messages belong in this exception."""


def _integer(value):
    return type(value) is int and value >= 0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _validate(report):
    """Check the v2 structures we consume before caching, without guessing data."""
    if (not isinstance(report, dict) or report['schema_version'] != 2
            or not all(isinstance(report[key], str) for key in ('generated_at', 'notice'))
            or not isinstance(report['rules'], dict)
            or not isinstance(report['products'], list)
            or not isinstance(report['supplier_coverage'], list)
            or not isinstance(report['followups'], dict)):
        raise ValueError
    summary = report['summary']
    counts = [summary['total'], summary['ddp_ready'], summary['ddp_estimable'],
              summary['with_conflict'], *[summary['by_status'][key] for key in STATUS_TEXT],
              *[summary['by_conflict_reason'][key] for key in CONFLICT_TEXT]]
    if not all(_integer(value) for value in counts):
        raise ValueError
    for product in report['products']:
        if not all(isinstance(product[key], str) for key in
                   ('equipment_id', 'supplier', 'label', 'source_file')):
            raise ValueError
        comp, facts = product['completeness'], product['facts']
        if (comp['status'] not in STATUS_TEXT
                or comp['conflict_reason'] not in (None, *CONFLICT_TEXT)
                or not isinstance(comp['missing'], list)
                or not all(isinstance(label, str) for label in comp['missing'])
                or not isinstance(facts, dict)):
            raise ValueError
        for kind, group in facts.items():
            if (kind not in FACT_KINDS or group['kind'] != kind
                    or type(group['conflict']) is not bool
                    or group['conflict_reason'] not in (None, *CONFLICT_TEXT)
                    or not isinstance(group['candidates'], list) or not group['candidates']):
                raise ValueError
            for candidate in group['candidates']:
                value = candidate['value']
                if kind.endswith('_size'):
                    valid = isinstance(value, list) and len(value) == 3 and all(map(_number, value))
                elif kind == 'units_per_carton':
                    valid = _integer(value) and value > 0
                else:
                    valid = _number(value)
                if not valid:
                    raise ValueError
    suppliers = {product['supplier'] for product in report['products']}
    coverage_suppliers = []
    for entry in report['supplier_coverage']:
        coverage_suppliers.append(entry['supplier'])
        if (not isinstance(entry['supplier'], str)
                or not all(_integer(entry[key]) for key in (
                    'total', 'with_packing', 'without_packing', 'ddp_estimable', 'ddp_ready'))
                or type(entry['coverage_rate']) not in (int, float)
                or not 0 <= entry['coverage_rate'] <= 1):
            raise ValueError
    if (set(coverage_suppliers) != suppliers or len(coverage_suppliers) != len(suppliers)
            or not all(isinstance(key, str) and isinstance(value, str)
                       for key, value in report['followups'].items())):
        raise ValueError


def _reject_constant(value):
    raise ValueError


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


class _ReportCache:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.stamp = self.report = None

    @staticmethod
    def _stamp(stat):
        return stat.st_mtime_ns, stat.st_size, stat.st_dev, stat.st_ino

    def read(self):
        # One parse per revision, including concurrent requests. No stale report
        # is served after removal, oversize input or a broken replacement.
        with self.lock:
            try:
                stat = self.path.stat()
            except FileNotFoundError:
                self.stamp = self.report = None
                return None
            stamp = self._stamp(stat)
            if stat.st_size > MAX_BYTES:
                self.stamp = self.report = None
                raise ValueError
            if stamp == self.stamp:
                return self.report
            self.stamp = self.report = None
            with self.path.open('rb') as handle:
                before = self._stamp(os.fstat(handle.fileno()))
                data = handle.read(MAX_BYTES + 1)
                after = self._stamp(os.fstat(handle.fileno()))
            if len(data) > MAX_BYTES or before != after:
                raise ValueError
            report = json.loads(data, parse_constant=_reject_constant, parse_float=_finite_float)
            _validate(report)
            self.stamp, self.report = after, report
            return report


def _read_contacts(path):
    if not path.exists():
        return {}  # A GET must never create the database or its table.
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=1')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='supplier_followups'").fetchone():
            return {}
        rows = db.execute('SELECT supplier, contacted_at, note FROM supplier_followups '
                          'ORDER BY contacted_at DESC, id DESC')
        latest = {}
        for supplier, contacted_at, note in rows:
            if supplier not in latest:
                latest[supplier] = (contacted_at, note)
        return latest


def _record_contact(path, supplier, note):
    # This is solely a manual "I already contacted them" record, never a send.
    # Keep the process-wide umask window short and serialize our writers.
    with _STATE_LOCK:
        previous = os.umask(0o077)
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            path.chmod(0o600)
        finally:
            os.umask(previous)
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS supplier_followups ('
                           'id INTEGER PRIMARY KEY, supplier TEXT, contacted_at REAL, note TEXT)')
                contacted_at = time.time()
                db.execute('INSERT INTO supplier_followups (supplier, contacted_at, note) VALUES (?, ?, ?)',
                           (supplier, contacted_at, note))
        return contacted_at


def _single(params, key, default=''):
    if hasattr(params, 'getlist') and key in params and len(params.getlist(key)) != 1:
        raise _BadRequest('同一参数只能传入一次')
    value = params.get(key, default)
    if not isinstance(value, str):
        raise _BadRequest('请求参数须为文本')
    return value


def _supplier(params, required=False):
    supplier = _single(params, 'supplier')
    if len(supplier) > 500 or (required and not supplier.strip()):
        raise _BadRequest('请提供有效的供应商名称（不超过 500 字）')
    return supplier


def _parameters(params):
    raw_page, raw_size = _single(params, 'page', '1'), _single(params, 'page_size', '30')
    if not re.fullmatch(r'[0-9]{1,7}', raw_page) or not re.fullmatch(r'[0-9]{1,3}', raw_size):
        raise _BadRequest('页码和每页数量须为有效正整数')
    page, page_size = int(raw_page), int(raw_size)
    if page < 1 or not 1 <= page_size <= 100:
        raise _BadRequest('页码须大于零，每页数量须为 1 至 100')
    status, q = _single(params, 'status'), _single(params, 'q')
    if status and status not in STATUS_TEXT:
        raise _BadRequest('包装资料状态无效')
    if len(q) > 500:
        raise _BadRequest('搜索词不能超过 500 字')
    return dict(supplier=_supplier(params), status=status, q=q, page=page, page_size=page_size)


def _item(product):
    comp, facts = product['completeness'], product['facts']
    values = {}
    for field, kind in FACT_FIELDS.items():
        group = facts.get(kind)
        # A non-conflicting first candidate is a display value, not confirmation.
        values[field] = (group['candidates'][0]['value'] if group and not group['conflict'] else None)
    reason = comp['conflict_reason']
    return dict(id=product['equipment_id'], label=product['label'], supplier=product['supplier'],
                source_file=product['source_file'], fixture_type=product.get('fixture_type'),
                wattage=product.get('wattage'), status=comp['status'], status_text=STATUS_TEXT[comp['status']],
                conflict_reason=reason, conflict_text=CONFLICT_TEXT.get(reason), facts=values,
                n_candidates={kind: len(group['candidates']) for kind, group in facts.items()},
                missing=comp['missing'], assumption=(
                    '按每箱 1 台估算，须向供应商确认' if comp['status'] == 'estimable' else None))


def _summary(report, contacts):
    summary = report['summary']
    totals = dict(entries=summary['total'], **{key: summary['by_status'][key] for key in STATUS_TEXT},
                  multi_model=summary['by_conflict_reason']['multi_model_suspected'],
                  value_mismatch=summary['by_conflict_reason']['value_mismatch'])
    now = time.time()
    suppliers = []
    for entry in report['supplier_coverage']:
        last, note = contacts.get(entry['supplier'], (None, None))
        suppliers.append({key: entry[key] for key in (
            'supplier', 'total', 'with_packing', 'without_packing', 'coverage_rate')})
        suppliers[-1].update(estimable=entry['ddp_estimable'], ready=entry['ddp_ready'],
                             last_contacted=last, contacted_note=note)

    def recent(entry):
        last = entry['last_contacted']
        return last is not None and 0 <= now - last < 7 * 86400

    suppliers.sort(key=lambda entry: (recent(entry), -entry['without_packing'],
                                      entry['coverage_rate'], entry['supplier']))
    pending = Counter(product['supplier'] for product in report['products']
                      if product['completeness']['status'] != 'ready')
    actions = []
    for priority, entry in enumerate(suppliers, 1):
        entry['priority'] = priority
        supplier = entry['supplier']
        if len(actions) == 3 or not report['followups'].get(supplier):
            continue
        count = entry['without_packing'] or pending[supplier]
        reason = (f'{count} 条资料没有任何包装信息' if entry['without_packing']
                  else f'{count} 条资料的包装信息仍须补充或确认')
        if recent(entry):
            days = int((now - entry['last_contacted']) // 86400)
            reason += f'，{days} 天前已催过'
        actions.append(dict(supplier=supplier, reason=reason, count=count))
    return dict(ready=True, generated_at=report['generated_at'], notice=report['notice'],
                totals=totals, suppliers=suppliers, next_actions=actions)


def _authorised(request):
    principal = getattr(request.state, 'password_principal', None)
    recheck = getattr(request.state, 'password_recheck', None)
    legacy = getattr(request.state, 'session', None)
    return bool(principal and getattr(principal, 'username', None) and callable(recheck)
                and legacy and getattr(legacy, 'authenticated', False))


async def _recheck(request):
    try:
        return bool(await run_in_threadpool(request.state.password_recheck))
    except Exception:
        return False


def build(app_module, report_path=REPORT_PATH, state_path=STATE_PATH):
    cache, state = _ReportCache(report_path), Path(state_path)

    def read(kind, values):
        report = cache.read()
        if report is None:
            return {'ready': False}, 200
        if kind == 'summary':
            return _summary(report, _read_contacts(state)), 200
        supplier = values['supplier']
        if kind == 'followup':
            if supplier not in report['followups']:
                return {'error': '未找到该供应商的追问草稿'}, 404
            lines = report['followups'][supplier].splitlines()
            last, note = _read_contacts(state).get(supplier, (None, None))
            return dict(supplier=supplier, lines=lines, text='\n'.join(lines),
                        last_contacted=last, contacted_note=note), 200
        if supplier and supplier not in {entry['supplier'] for entry in report['supplier_coverage']}:
            return {'error': '供应商不在包装报告中'}, 400
        query, status = values['q'].casefold(), values['status']
        products = [product for product in report['products'] if (
            (not supplier or product['supplier'] == supplier)
            and (product['completeness']['status'] == status if status
                 else product['completeness']['status'] != 'missing')
            and (not query or any(query in product[key].casefold()
                                  for key in ('label', 'source_file', 'supplier'))))]
        products.sort(key=lambda product: (STATUS_ORDER[product['completeness']['status']], product['label']))
        start = (values['page'] - 1) * values['page_size']
        return dict(items=[_item(product) for product in products[start:start + values['page_size']]],
                    total=len(products), page=values['page'], page_size=values['page_size']), 200

    def handler(kind):
        async def serve(request):
            if not _authorised(request):
                return app_module.error_response(request, 403)
            try:
                values = (_parameters(request.query_params) if kind == 'items' else
                          {'supplier': _supplier(request.query_params, required=True)}
                          if kind == 'followup' else {})
            except _BadRequest as error:
                payload, code = {'error': str(error)}, 400
            else:
                try:
                    payload, code = await run_in_threadpool(read, kind, values)
                except Exception:
                    payload, code = {'error': '包装报告暂时无法读取'}, 503
            # Recheck even for missing data, bad parameters and read failures.
            if not await _recheck(request):
                return app_module.error_response(request, 403)
            return JSONResponse(payload, status_code=code, headers=HEADERS)
        return serve

    def write(supplier, note):
        report = cache.read()
        if report is None:
            return {'error': '包装报告尚未生成，暂无法记录已催'}, 503
        if supplier not in {entry['supplier'] for entry in report['supplier_coverage']}:
            return {'error': '供应商不在包装报告中'}, 400
        last = _record_contact(state, supplier, note)
        return dict(ok=True, supplier=supplier, last_contacted=last), 200

    async def contacted(request):
        if not _authorised(request) or not await _recheck(request):
            return app_module.error_response(request, 403)
        try:
            form = getattr(request.state, 'form', None)
            if form is None:
                form = parse_qs((await request.body()).decode('utf8', 'replace'),
                                keep_blank_values=True, max_num_fields=16)
            fields = {}
            for key in ('supplier', 'note'):
                values = form.get(key, [''])
                if not isinstance(values, (list, tuple)) or len(values) != 1 or not isinstance(values[0], str):
                    raise _BadRequest('供应商和备注须各传入一个文本值')
                fields[key] = values[0]
            supplier = _supplier(fields, required=True)
            if len(fields['note']) > 200:
                raise _BadRequest('备注不能超过 200 字')
        except (ValueError, TypeError, AttributeError) as error:
            payload, code = {'error': str(error) if isinstance(error, _BadRequest) else '表单参数无效'}, 400
        else:
            try:
                payload, code = await run_in_threadpool(write, supplier, fields['note'] or None)
            except Exception:
                payload, code = {'error': '已催记录暂时无法保存'}, 503
        return JSONResponse(payload, status_code=code, headers=HEADERS)

    return [Route('/api/v1/packing/summary', handler('summary'), methods=['GET'], name='packing_summary'),
            Route('/api/v1/packing/items', handler('items'), methods=['GET'], name='packing_items'),
            Route('/api/v1/packing/followup', handler('followup'), methods=['GET'], name='packing_followup'),
            Route('/api/v1/packing/contacted', contacted, methods=['POST'], name='packing_contacted')]
