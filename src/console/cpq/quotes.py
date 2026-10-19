'''Server-owned freezing with a separate customer projection.'''
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from string import Formatter
import json
import re
import secrets
import sqlite3
from .company import CompanyProfile, DEFAULT_PROFILE, load_company_profile, payment_terms, validate_company_profile
from .dto import build_customer_dto
from .leak_guard import load_leak_context, scan
from .models import json_value
from .service import CPQError, CPQService, obj, require
from .store import PACK_FIELDS, dump, stamp

CALC_FIELDS = set('lines shipping target_margin pricing_mode acks override_reason'.split())
QUOTE_FIELDS = set('buyer payment_terms_id lead_time_days draft'.split())
RESULT_FIELDS = set('result results totals breakdown fcl_plan packing_compare alerts gate engine_version cost_masked amount amount_usd price_usd total_amount packages cbm chargeable_kg logistics_alloc_usd unit_goods_value_cny actual_margin freight_ratio quote_token quote_no valid_until customer_dto customer_dto_json internal_snapshot internal_snapshot_json inputs_json result_json exports'.split())
SOFT = {'ACK_REQUIRED', 'OVERRIDE_REQUIRED', 'IMAGE_MISSING'}
CATEGORIES = {'supplier_name', 'factory_model', 'cny_marker', 'internal_key', 'cost_value'}
ITEM_FIELDS = 'product_name light_source pixel_segments dmx_modes control_protocols power_w ip_rating voltage net_weight_kg dimensions_cm certifications'.split()
TOKEN = re.compile(r'q_[0-9a-f]{32}\Z')


class _Calculation(CPQService):
    '''Per-call adapter around the accepted CPQService.calculate implementation.

    Full serialization needs view_cost; preparation explicitly retains actual
    caller permissions for validation and gates. Shared service state is untouched.
    '''
    def __init__(self, service, permissions):
        self.original = service
        self.store, self.repository = service.store, service.repository
        self._config, self.today = service.config, service.today
        self.permissions = frozenset(permissions)
        self.inputs, self.sources = None, {}

    def _model(self, mid, today):
        model, offers, profiles, cost = self.original._model(mid, today)
        self.sources[mid] = deepcopy({'model': model, 'offers': offers, 'packing': profiles, 'basis': self.store.basis(offers), 'case_cost_cny': cost})
        return model, offers, profiles, cost

    def prepare_calculation(self, payload, permissions):
        self.inputs, context = super().prepare_calculation(payload, self.permissions)
        return self.inputs, context

    def full_result(self, payload):
        return super().calculate(payload, self.permissions | {'cpq.view_cost'})


class _LeakBlocked(Exception):
    def __init__(self, categories):
        self.categories = sorted(set(categories))
        super().__init__('LEAK_BLOCKED')


def _private(key):
    text = key.casefold()
    return any(word in text for word in ('cost', 'supplier', 'offer', '成本', '出厂', '供应商', '底价')) or text.startswith('exw')


def _reject(payload):
    pending, objects = [payload], []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            objects.append(value)
            for key, child in value.items():
                if not isinstance(key, str):
                    raise CPQError(400, 'BAD_REQUEST')
                if key.casefold() in RESULT_FIELDS:
                    raise CPQError(400, 'CLIENT_RESULT_REJECTED')
                pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)
    if any(_private(key) for value in objects for key in value):
        raise CPQError(400, 'CLIENT_COST_REJECTED')


def _categories(text, context):
    try:
        categories = {hit.category for hit in scan(text, context)}
        if categories - CATEGORIES:
            raise ValueError('Unknown inspection category')
        return sorted(categories)
    except Exception:
        raise _LeakBlocked(['inspection_error']) from None


def _clean(value, context):
    if isinstance(value, dict):
        return {key: _clean(child, context) for key, child in value.items() if not _private(key)}
    if isinstance(value, list):
        return [_clean(child, context) for child in value]
    return '[redacted]' if isinstance(value, str) and _categories(value, context) else value


def _stored_result(result):
    # Full calculations, including costs, live only in the private snapshot.
    return {
        'engine_version': result['engine_version'], 'cost_masked': True,
        'lines': [{key: row[key] for key in 'model_id qty packages cbm gw_kg chargeable_kg unit_price_usd amount_usd'.split()} for row in result['lines']],
        'totals': {key: result['totals'][key] for key in 'packages cbm gw_kg chargeable_kg price_usd'.split()},
        'gate': result['gate'],
        'alerts': [{key: row.get(key) for key in ('code', 'severity', 'requires')} for row in result['alerts']],
    }


class QuoteService:
    def __init__(self, settings, *, service=None, company_profile=None, company_profile_path=None):
        self.service = service if service is not None else CPQService(settings)
        self.store, self.repository = self.service.store, self.service.repository
        self._company_profile = company_profile
        self.company_profile_path = company_profile_path or getattr(settings, 'company_profile_path', DEFAULT_PROFILE)

    def _profile(self):
        raw = self._company_profile
        if isinstance(raw, CompanyProfile):
            raw = raw.values
        return validate_company_profile(raw, self.service.config) if raw is not None else load_company_profile(self.company_profile_path, config=self.service.config)

    @staticmethod
    def _validate(payload):
        _reject(payload)
        obj(payload, CALC_FIELDS | QUOTE_FIELDS, {'lines', 'shipping', 'pricing_mode'})
        if type(payload.get('draft', False)) is not bool:
            raise CPQError(400, 'BAD_REQUEST')
        buyer = obj(payload.get('buyer', {}), {'company', 'contact', 'country'})
        if any(not isinstance(value, str) for value in buyer.values()):
            raise CPQError(400, 'BAD_REQUEST')
        payment = payload.get('payment_terms_id')
        if payment is not None and (not isinstance(payment, str) or not payment.strip() or payment != payment.strip()):
            raise CPQError(400, 'BAD_REQUEST')
        leads = payload.get('lead_time_days', {})
        if not isinstance(leads, dict):
            raise CPQError(400, 'BAD_REQUEST')
        for mid, lead in leads.items():
            if not isinstance(mid, str) or not isinstance(lead, str) or not re.fullmatch(r'[0-9]+(?:-[0-9]+)?', lead):
                raise CPQError(400, 'BAD_REQUEST')
            try:
                bounds = [int(part) for part in lead.split('-')]
            except ValueError:
                raise CPQError(400, 'BAD_REQUEST') from None
            if min(bounds) <= 0 or bounds[-1] < bounds[0]:
                raise CPQError(400, 'BAD_REQUEST')
        return {key: deepcopy(value) for key, value in payload.items() if key in CALC_FIELDS}

    def _gate(self, result, payload, permissions, draft):
        gate = deepcopy(result['gate'])
        original = list(dict.fromkeys(gate['blocked_by']))
        if not gate['can_save']:
            blocked = original or ['QUOTE_BLOCKED']
            raise CPQError(409, blocked[0], {'blocked_by': blocked})
        soft_alerts = SOFT | set(gate['requires_ack']) | {'MARGIN_BELOW_MIN'}
        blocked = [code for code in original if code not in soft_alerts]
        if set(gate['requires_ack']) - set(payload.get('acks', [])):
            blocked.append('ACK_REQUIRED')
        minimum = max(10, self.service.config['thresholds']['override_reason_min_chars'])
        if gate['requires_override'] and ('cpq.override_margin' not in permissions or len(payload.get('override_reason', '').strip()) < minimum):
            blocked.append('OVERRIDE_REQUIRED')
        if 'IMAGE_MISSING' in original or any(row['code'] == 'IMAGE_MISSING' for row in result['alerts']):
            blocked.append('IMAGE_MISSING')
        blocked = list(dict.fromkeys(blocked))
        if not gate['can_export'] and not blocked:
            blocked = ['QUOTE_BLOCKED']
        remaining = [code for code in blocked if not draft or code not in SOFT]
        if remaining:
            raise CPQError(409, remaining[0], {'blocked_by': remaining})
        gate.update(blocked_by=blocked, can_export=not draft and not blocked)
        return gate

    @staticmethod
    def _number(connection, today):
        prefix = f'Q{today:%y%m%d}-'
        seq = connection.execute('''SELECT COALESCE(MAX(CAST(substr(quote_no,9) AS INTEGER)),0)+1
            FROM cpq_quote WHERE substr(quote_no,1,8)=? AND length(quote_no)>8
            AND substr(quote_no,9) NOT GLOB '*[^0-9]*' ''', (prefix,)).fetchone()[0]
        return f'{prefix}{seq:03d}'

    def _skus(self, connection, mids, profile, created_at):
        existing = dict(connection.execute('SELECT model_id,public_sku FROM cpq_public_sku'))
        if all(mid in existing for mid in mids):
            return {mid: existing[mid] for mid in mids}
        public = self.service.config.get('public_sku', {})
        if not isinstance(public, Mapping):
            raise ValueError('Invalid public SKU configuration')
        pattern, brand = public.get('pattern'), public.get('brand', profile.values['brand'])
        if not isinstance(pattern, str) or not pattern or not isinstance(brand, str) or not brand.strip():
            raise ValueError('Public SKU pattern and brand required')
        names, sequence = set('seq index n num number sequence'.split()), False
        for _, field, spec, conversion in Formatter().parse(pattern):
            if field is not None:
                if field not in names | {'brand'} or '{' in spec or '}' in spec or conversion:
                    raise ValueError('Unsupported public SKU placeholder')
                sequence = sequence or field in names
        if not sequence:
            raise ValueError('Public SKU pattern needs a sequence')
        used = set(existing.values())
        for mid in mids:
            if mid in existing:
                continue
            for seq in range(1, len(used) + 2):
                sku = pattern.format(brand=brand, **{key: seq for key in names})
                if sku and sku not in used:
                    break
            else:
                raise ValueError('Public SKU pattern cannot produce a unique value')
            connection.execute('INSERT INTO cpq_public_sku(model_id,public_sku,created_at) VALUES (?,?,?)', (mid, sku, created_at))
            existing[mid] = sku
            used.add(sku)
        return {mid: existing[mid] for mid in mids}

    def _context(self, mids, inputs):
        try:
            context = load_leak_context(self.store.db_path, self.store.work_db, mids)
            costs = set(context.cost_values)
            for line in inputs.lines:
                values = [line.cost_basis_cny, line.case_cost_cny]
                values.extend(a.cost_cny for a in line.accessories)
                values.extend(p.case_cost_cny for p in (line.packing.carton, line.packing.flight_case, line.packing.override) if p is not None)
                costs.update(value for value in values if value is not None)
            return replace(context, cost_values=tuple(sorted(costs)))
        except Exception:
            raise _LeakBlocked(['inspection_error']) from None

    def _snapshot(self, payload, inputs, result, skus, profile, payment, number, until):
        items, images, lines = {}, {}, []
        accessories = {row['accessory_id']: row['name_en'] for row in self.store.accessories()}
        for line in inputs.lines:
            mid = line.model_id
            if mid in items:
                continue
            source = self.repository.get_strobe(mid)
            if source is None:
                raise CPQError(404, 'MODEL_NOT_FOUND', {'model_id': mid})
            items[mid] = {key: deepcopy(source.get(key)) for key in ITEM_FIELDS}
            images[mid] = [im['asset_uid'] for im in self.repository.images_for(mid, customer_safe_only=True) if im.get('customer_safe') is True]
            if not images[mid]:
                raise CPQError(409, 'IMAGE_MISSING', {'blocked_by': ['IMAGE_MISSING']})
        for requested, line, computed in zip(payload['lines'], inputs.lines, result['lines'], strict=True):
            item = deepcopy(items[line.model_id])
            item['internal_sku'] = skus[line.model_id]
            packing = line.packing.override or (line.packing.flight_case if line.packing_mode == 'flight_case' else line.packing.carton)
            if packing is None:
                raise ValueError('Effective packing unavailable')
            item[line.packing_mode] = {key: getattr(packing, key) for key in PACK_FIELDS[:-1]}
            lines.append({
                'item': item, 'qty': line.qty, 'packing_mode': line.packing_mode,
                'accessories': [{'name_en': accessories[a['accessory_id']], 'qty_per_unit': a['qty_per_unit']} for a in requested.get('accessories', [])],
                'lead_time': payload.get('lead_time_days', {}).get(line.model_id, profile.values['default_lead_time']),
                'result': {key: computed[key] for key in 'packages cbm gw_kg unit_price_usd amount_usd'.split()},
                'image_uids': images[line.model_id],
            })
        return {
            'quote_no': number, 'issue_date': inputs.today.isoformat(), 'valid_until': until,
            'seller': profile.seller, 'buyer': payload.get('buyer', {}),
            'shipping': {'mode': inputs.shipping.mode, 'incoterm': inputs.shipping.incoterm, 'named_place': inputs.shipping.lane.named_place},
            'terms': {'payment': payment, 'lead_time': profile.values['default_lead_time'], 'warranty': profile.values['warranty'], 'price_includes': profile.values['price_includes'], 'validity': until},
            'lines': lines, 'totals': {key: result['totals'][key] for key in 'price_usd packages cbm gw_kg'.split()},
        }

    def freeze(self, payload, permissions, actor):
        permissions = frozenset(permissions)
        require(permissions, 'cpq.quote')
        if not isinstance(actor, str) or not actor.strip():
            raise CPQError(401, 'UNAUTHENTICATED')
        calculation = self._validate(payload)
        run = _Calculation(self.service, permissions)
        result = run.full_result(calculation)
        draft = payload.get('draft', False)
        gate = self._gate(result, calculation, permissions, draft)
        inputs = run.inputs
        if inputs is None:
            raise CPQError(409, 'FX_EXPIRED', {'blocked_by': ['FX_EXPIRED']})
        mids = tuple(dict.fromkeys(line.model_id for line in inputs.lines))
        if set(payload.get('lead_time_days', {})) - set(mids):
            raise CPQError(400, 'BAD_REQUEST')
        profile, terms = self._profile(), payment_terms(self.service.config)
        payment_id = payload.get('payment_terms_id') or profile.values['payment_terms']
        if payment_id and payment_id not in terms:
            raise CPQError(400, 'PAYMENT_TERMS_UNKNOWN')
        if not draft and not payment_id:
            raise CPQError(400, 'PAYMENT_TERMS_REQUIRED')
        dates = [inputs.today + timedelta(days=inputs.config['pricing']['quote_validity_days']), inputs.shipping.lane.valid_until]
        dates.extend(date.fromisoformat(o['valid_until']) for source in run.sources.values() for o in source['offers'] if o['valid_until'])
        until, token, created_at = min(dates).isoformat(), 'q_' + secrets.token_hex(16), stamp()
        status = 'draft' if draft else 'final'
        self.store.initialize()
        connection = sqlite3.connect(self.store.work_db, timeout=60.0, isolation_level=None)
        try:
            connection.execute('BEGIN IMMEDIATE')
            number = self._number(connection, inputs.today)
            skus = self._skus(connection, mids, profile, created_at)
            context = self._context(mids, inputs)
            customer_json = None
            if not draft:
                snapshot = self._snapshot(payload, inputs, result, skus, profile, terms[payment_id], number, until)
                try:
                    customer_json = dump(build_customer_dto(snapshot))
                except Exception:
                    raise _LeakBlocked(['projection_error']) from None
                categories = _categories(customer_json, context)
                if categories:
                    raise _LeakBlocked(categories)
            clean = _clean(deepcopy(payload), context)
            internal = {'result': result, 'calculation_inputs': json_value(inputs), 'sources': run.sources, 'request': deepcopy(payload)}
            connection.execute('''INSERT INTO cpq_quote(quote_token,quote_no,status,engine_version,inputs_json,result_json,
                internal_snapshot_json,customer_dto_json,acks_json,override_reason,created_by,created_at,valid_until)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (token, number, status, result['engine_version'], dump(clean), dump(_stored_result(result)), dump(internal), customer_json,
                 dump(clean.get('acks', [])), clean.get('override_reason', '').strip(), actor.strip(), created_at, until))
            actions = ['QUOTE_FROZEN']
            if not draft and result['gate']['requires_override']:
                actions.append('MARGIN_OVERRIDE')
            for action in actions:
                connection.execute('INSERT INTO cpq_audit_log(at,actor,action,quote_token,detail_json) VALUES (?,?,?,?,?)', (created_at, actor.strip(), action, token, dump({'status': status})))
            connection.commit()
        except _LeakBlocked as exc:
            connection.rollback()
            self.store.append_audit('LEAK_BLOCKED', actor=actor.strip(), quote_token=token, detail={'categories': exc.categories})
            raise CPQError(422, 'LEAK_BLOCKED', {'categories': exc.categories}) from None
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        exports = None if draft else {key: f'/quotes/{token}/{name}' for key, name in (
            ('pdf', 'customer.pdf'), ('html', 'customer.html'), ('whatsapp', 'whatsapp.txt'), ('images_zip', 'images.zip'))}
        response = {'quote_token': token, 'quote_no': number, 'valid_until': until, 'status': status, 'gate': gate, 'exports': exports}
        if profile.incomplete_fields:
            response['profile_incomplete'] = list(profile.incomplete_fields)
        return response

    def get_internal(self, quote_token, permissions):
        require(permissions, 'cpq.admin')
        if not isinstance(quote_token, str) or not TOKEN.fullmatch(quote_token):
            raise CPQError(404, 'QUOTE_NOT_FOUND')
        rows = self.store.read('SELECT * FROM cpq_quote WHERE quote_token=?', (quote_token,))
        if not rows:
            raise CPQError(404, 'QUOTE_NOT_FOUND')
        row = rows[0]
        for key in 'inputs_json result_json internal_snapshot_json customer_dto_json acks_json'.split():
            row[key.removesuffix('_json')] = json.loads(row[key]) if row[key] is not None else None
        row['result'] = row['internal_snapshot']['result']
        return row

    def get_public_row(self, quote_token):
        if not isinstance(quote_token, str) or not TOKEN.fullmatch(quote_token):
            raise CPQError(404, 'QUOTE_NOT_FOUND')
        rows = self.store.read('SELECT status,customer_dto_json,quote_no,valid_until FROM cpq_quote WHERE quote_token=?', (quote_token,))
        if not rows:
            raise CPQError(404, 'QUOTE_NOT_FOUND')
        return rows[0]
