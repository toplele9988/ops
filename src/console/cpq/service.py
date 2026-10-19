"""Server-owned inputs and permission-filtered CPQ responses."""
from dataclasses import replace
from datetime import date
from pathlib import Path
from math import isfinite
from console.strobe.repository import StrobeRepository
from . import engine, gates
from .config import config_from_dict, load_config
from .models import Accessory, CalcInputs, GateContext, LineInput, PackingProfiles, ShippingInput, json_value
from .store import CPQStore, PACK_FIELDS, num, pack

CONFIG = Path(__file__).resolve().parents[2] / 'config/cpq.yaml'
TERMS = {'sea_lcl': {'FOB', 'CIF', 'DDP'}, 'sea_fcl': {'FOB', 'CIF', 'DDP'}, 'express': {'DAP', 'DDP'}}


class CPQError(Exception):
    def __init__(self, status, code, details=None):
        super().__init__(code)
        self.status_code, self.code, self.details = status, code, details


def require(permissions, name):
    if name not in permissions:
        raise CPQError(403, 'FORBIDDEN', {'required': name})


def obj(value, allowed, required=()):
    if not isinstance(value, dict) or set(value)-set(allowed) or not set(required)<=set(value):
        raise CPQError(400, 'BAD_REQUEST')
    return value


def text(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CPQError(400, 'BAD_REQUEST')


def numeric(value, positive=False):
    try:
        return num(value, positive)
    except ValueError as exc:
        raise CPQError(400, 'BAD_REQUEST') from exc


def reject_costs(value, permissions):
    pending, needs_cost = [(value, ())], False
    while pending:
        value, path = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise CPQError(400, 'BAD_REQUEST')
                if key == 'exw_cny' or key.startswith(('cost_', 'supplier_', 'offer_')):
                    raise CPQError(400, 'CLIENT_COST_REJECTED')
                if key == 'case_cost_cny':
                    if not (len(path)==3 and path[0]=='lines' and type(path[1]) is int and path[2]=='packing_override'):
                        raise CPQError(400, 'CLIENT_COST_REJECTED')
                    needs_cost = True
                pending.append((child, (*path, key)))
        elif isinstance(value, list):
            pending.extend((v, (*path, i)) for i, v in enumerate(value))
    if needs_cost:
        require(permissions, 'cpq.view_cost')


def mask(value):
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items() if k=='cost_masked' or ('cost' not in k.lower() and k!='unit_goods_value_cny' and not k.startswith('exw'))}
    return [mask(v) for v in value] if isinstance(value, list) else value


class CPQService:
    def __init__(self, settings, *, config=None, today=None):
        self.store = CPQStore(settings.db_path, settings.work_db)
        self.repository = StrobeRepository(settings.db_path, settings.work_db, settings.media_uid_secret)
        self._config = config_from_dict(config) if config is not None else None
        self.today = today or date.today

    @property
    def config(self):
        if self._config is None:
            self._config = load_config(CONFIG)
        return self._config

    def _model(self, model_id, today):
        model = self.store.model(model_id)
        if model is None:
            raise CPQError(404, 'MODEL_NOT_FOUND', {'model_id': model_id})
        offers, profiles = self.store.offers(model_id, today), self.store.packing(model_id)
        cost = self.store.case_cost(model_id, offers)
        carton, flight = profiles['carton'], profiles['flight_case']
        if flight is not None:
            flight = {**flight, 'case_cost_cny': cost, 'case_cost_missing': cost is None}
        elif carton is not None and model['net_weight_kg'] is not None:
            estimate = self.config['packing_estimate']
            flight = {'units_per_pkg': carton['units_per_pkg'], **{k: carton[k]+estimate['fc_pad_cm'] for k in ('l_cm','w_cm','h_cm')}, 'gw_kg': carton['units_per_pkg']*num(model['net_weight_kg'])+estimate['fc_tare_kg'], 'case_cost_cny': cost, 'case_cost_missing': cost is None, 'source': 'estimated'}
        return model, offers, {'carton': carton, 'flight_case': flight}, cost

    def build_context(self, model_ids, permissions):
        require(permissions, 'cpq.quote')
        if not isinstance(model_ids, (list, tuple)) or not 0<=len(model_ids)<=50 or any(not isinstance(v,str) or not v.strip() or v!=v.strip() for v in model_ids):
            raise CPQError(400, 'BAD_MODEL_IDS')
        today, config, visible = self.today(), self.config, 'cpq.view_cost' in permissions
        fx = self.store.fx(today)
        if fx is not None:
            fx['buffer_pct'] = config['pricing']['fx_buffer_pct']
        models = []
        for model_id in dict.fromkeys(model_ids):
            _, offers, packing, _ = self._model(model_id, today)
            item = {'model_id': model_id, 'packing': packing}
            if visible:
                basis = self.store.basis(offers)
                item.update({k: basis[k] for k in ('cost_basis_cny','cost_basis_mode','offer_valid_until')})
            if 'cpq.view_supplier' in permissions:
                item['offers'] = [{k: o[k] for k in ('supplier_id','supplier_name','exw_cny','valid_until')} for o in offers]
            models.append(item)
        result = {'fx': fx, 'fx_missing': fx is None, **{k: json_value(config[k]) for k in ('thresholds','pricing','containers','payment_terms') if k in config}, 'lanes': [json_value(l) for l in self.store.lanes()], 'accessories': self.store.accessories(), 'models': models, 'cost_masked': not visible, 'permissions': sorted(permissions)}
        return result if visible else mask(result)

    def _validate(self, payload, permissions):
        reject_costs(payload, permissions)
        body = obj(payload, {'lines','shipping','target_margin','pricing_mode','acks','override_reason'}, {'lines','shipping','pricing_mode'})
        shipping = obj(body['shipping'], {'mode','incoterm','lane_id'}, {'mode','incoterm','lane_id'})
        mode, term = shipping['mode'], shipping['incoterm']
        if not isinstance(mode,str) or not isinstance(term,str) or mode not in TERMS or term not in TERMS[mode]:
            raise CPQError(400, 'BAD_INCOTERM')
        text(shipping['lane_id'])
        if body['pricing_mode'] not in ('margin','price') or not isinstance(body['lines'],list) or not body['lines']:
            raise CPQError(400, 'BAD_REQUEST')
        for row in body['lines']:
            obj(row, {'model_id','qty','packing_mode','packing_override','accessories','unit_price_usd'}, {'model_id','qty','packing_mode'})
            text(row['model_id'])
            if type(row['qty']) is not int or row['qty']<=0 or row['packing_mode'] not in ('carton','flight_case'):
                raise CPQError(400, 'BAD_REQUEST')
            override = row.get('packing_override')
            if override is not None:
                obj(override, PACK_FIELDS, PACK_FIELDS[:-1])
                try:
                    pack(override)
                except ValueError as exc:
                    raise CPQError(400, 'BAD_REQUEST') from exc
            accessories = row.get('accessories', [])
            if not isinstance(accessories, list):
                raise CPQError(400, 'BAD_REQUEST')
            for accessory in accessories:
                obj(accessory, {'accessory_id','qty_per_unit'}, {'accessory_id','qty_per_unit'})
                text(accessory['accessory_id'])
                numeric(accessory['qty_per_unit'])
            if body['pricing_mode']=='price':
                numeric(row.get('unit_price_usd'), True)
            elif row.get('unit_price_usd') is not None:
                numeric(row['unit_price_usd'])
        margin = 0.0
        if body['pricing_mode']=='margin':
            margin = body.get('target_margin', self.config['thresholds']['min_margin'])
            try:
                valid = type(margin) in (int,float) and isfinite(margin) and -1<margin<1
            except OverflowError:
                valid = False
            if not valid:
                raise CPQError(400, 'BAD_REQUEST')
        acks = body.get('acks', [])
        if not isinstance(acks,list) or any(not isinstance(v,str) for v in acks) or not isinstance(body.get('override_reason',''),str):
            raise CPQError(400, 'BAD_REQUEST')
        return body, float(margin)

    def prepare_calculation(self, payload, permissions):
        permissions = frozenset(permissions)
        require(permissions, 'cpq.quote')
        body, margin = self._validate(payload, permissions)
        today, mode = self.today(), body['shipping']['mode']
        lane = self.store.lane(body['shipping']['lane_id'])
        if lane is None:
            raise CPQError(400, 'LANE_UNKNOWN')
        if lane.mode != ('express' if mode=='express' else 'sea'):
            raise CPQError(400, 'BAD_INCOTERM')
        accessories = {a['accessory_id']: a for a in self.store.accessories()}
        cache, images, missing, lines = {}, {}, {}, []
        for row in body['lines']:
            mid = row['model_id']
            if mid not in cache:
                cache[mid] = self._model(mid, today)
                images[mid] = any(i.get('customer_safe') is True for i in self.repository.images_for(mid, customer_safe_only=True))
            model, offers, profiles, cost = cache[mid]
            carton, flight, override = profiles['carton'], profiles['flight_case'], row.get('packing_override')
            fc_override = row['packing_mode']=='flight_case' and override is not None
            reasons = [f'carton.{k}' for k in PACK_FIELDS[:-1]] if carton is None else []
            if flight is None and not fc_override:
                if model['net_weight_kg'] is None:
                    reasons.append('net_weight_kg')
                if carton is None:
                    reasons.append('flight_case')
            if reasons:
                missing.setdefault(mid,set()).update(reasons)
                continue
            selected_accessories = []
            for accessory in row.get('accessories', []):
                stored = accessories.get(accessory['accessory_id'])
                if stored is None:
                    raise CPQError(400, 'ACCESSORY_UNKNOWN', {'accessory_id': accessory['accessory_id']})
                selected_accessories.append(Accessory(num(stored['cost_cny']), float(accessory['qty_per_unit'])))
            manual = None
            if override is not None:
                override = dict(override)
                if fc_override and 'case_cost_cny' not in override:
                    override['case_cost_cny'] = cost
                manual = pack(override)
            selected = flight if row['packing_mode']=='flight_case' else carton
            chosen_cost = manual.case_cost_cny if fc_override else cost
            lines.append(LineInput(model_id=mid, qty=row['qty'], packing_mode=row['packing_mode'], packing=PackingProfiles(pack(carton), pack(flight) if flight else None, manual), cost_basis_cny=self.store.basis(offers)['cost_basis_cny'], unit_price_usd=row.get('unit_price_usd'), accessories=tuple(selected_accessories), net_weight_kg=model['net_weight_kg'], packing_estimated=manual is not None or (selected is not None and selected['source']!='catalog'), case_cost_missing=row['packing_mode']=='flight_case' and chosen_cost is None, category=model['category'], spec_complete=model['spec_complete'], case_cost_cny=cost))
        if missing:
            raise CPQError(400, 'PACKING_UNKNOWN', [{'model_id': k, 'missing': sorted(v)} for k,v in missing.items()])
        ctx = GateContext(permissions, frozenset(body.get('acks',[])), body.get('override_reason',''), images)
        fx = self.store.fx(today)
        if fx is None:
            return None, ctx
        return CalcInputs(tuple(lines), ShippingInput(mode, body['shipping']['incoterm'], lane), fx['usd_cny'], fx['age_days'], None, margin, body['pricing_mode'], self.config, today), ctx

    def calculate(self, request_json, permissions):
        inputs, ctx = self.prepare_calculation(request_json, permissions)
        visible = 'cpq.view_cost' in permissions
        if inputs is None:
            return {'engine_version': engine.ENGINE_VERSION, 'lines': None, 'totals': None, 'breakdown': None, 'fcl_plan': None, 'packing_compare': None, 'cost_masked': not visible, 'alerts': [{'code':'FX_EXPIRED','severity':'fuse','requires':None,'message':'No FX rate available'}], 'gate': {'can_save':False,'can_export':False,'requires_ack':[],'requires_override':False,'blocked_by':['FX_EXPIRED']}}
        try:
            result = engine.compute(inputs)
            alerts, gate = gates.evaluate(result, inputs, ctx)
        except (ValueError, OverflowError) as exc:
            raise CPQError(400, 'CALCULATION_INVALID') from exc
        result = replace(result, alerts=alerts, gate=gate)
        response = result.to_dict()
        if result.fcl_plan is None and any(a.code=='LCL_FCL_SUGGEST' for a in alerts):
            plan = engine.best_fcl_plan(result.totals.cbm, result.totals.gw_kg, inputs.config['containers'], inputs.shipping.lane.fcl_rate_usd)
            response['fcl_plan'] = json_value(replace(plan, cost_usd=engine.round2(plan.cost_usd)))
        response['alerts'] = []
        for alert in alerts:
            item = {'code':alert.code,'severity':alert.level,'requires':'ack' if alert.code in gate.requires_ack else ('override' if alert.code=='MARGIN_BELOW_MIN' else None),'message':alert.message}
            if visible and alert.suggestions:
                item['suggestions'] = list(alert.suggestions)
            response['alerts'].append(item)
        response['cost_masked'] = not visible
        if not visible:
            # Fraction relative to all-carton cost; unknown remains null.
            base = response['packing_compare']['carton']['total_cost_usd']
            for comparison in response['packing_compare'].values():
                total = comparison['total_cost_usd']
                comparison['delta_pct'] = (total-base)/base if base is not None and base>0 and total is not None else None
            response = mask(response)
        return response
