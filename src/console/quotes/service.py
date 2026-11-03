"""Atomic quote snapshots and lifecycle; all reads are local and channel-free."""
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from console.archive import validation as v
from console.archive.service import get
from console.archive.store import ARCHIVE_PATH, Store, insert, update, audit
from console.archive.validity import (current_offer, current_quote, route_key, quote_state,
                                      validity, remaining_days)
from console.deals.service import Deals
from console.deals import operations
from . import validation
from .calc import compute
from .freight import calculate, PACKING
from .fx import FX_PATH, read_fx
from .money import money, rate
from .text import render
from .documents import Documents
from .storage import State, STATE_PATH

LOCKED = '这一轮已发出，请新建下一轮'
FREIGHT_CHANNEL_LABELS = {'platform_alibaba': '阿里国际站平台物流',
                          'forwarder': '外部货代', 'other': '其他渠道'}


def children(db, table, round_id):
    return [dict(r) for r in db.execute('SELECT * FROM ' + table + ' WHERE round_id=? ORDER BY id', (round_id,))]


def public_calc(calc):
    result = dict(calc)
    for key in ('unit_price_fen', 'min_charge_fen', 'amount_fen'):
        if key in result:
            result[key.removesuffix('_fen')] = money(result.pop(key))
    if 'quote' in result:
        quote = dict(result['quote'])
        for key in ('price_fen', 'min_charge_fen'):
            quote[key.removesuffix('_fen')] = money(quote.pop(key, None))
        result['quote'] = quote
    return result


class Quotes(Documents):
    def __init__(self, path=ARCHIVE_PATH, *, store=None, today=None, fx_path=FX_PATH, clock=None,
                 state_path=None, lang_service=None):
        # Preserve the supplied Store's connection/path without changing the
        # schema policy of other services sharing that Store object.
        self.store = Store(store.path, connection=store.connection, target_version=9) if store else Store(path, target_version=9)
        self.today = today or date.today
        self.clock = clock or time.time
        self.fx_path = fx_path
        self.deals = Deals(store=self.store, lookup=lambda key: None, clock=self.clock)
        default_state = STATE_PATH if self.store.path == ARCHIVE_PATH else self.store.path.with_suffix('.quote-state.sqlite3')
        if self.store.connection is not None:
            default_state = ':memory:'
        self.state = State(state_path if state_path is not None else os.environ.get('OPS_QUOTES_STATE_PATH', default_state))
        if self.state.path is not None and self.state.path.resolve() == self.store.path.resolve():
            raise ValueError('报价状态库必须与档案库使用不同文件')
        self.lang = lang_service

    def _language_service(self):
        if self.lang is None:
            from console.lang import Lang, LANG_PATH
            from console.channels import shared_inbox
            if self.store.path == ARCHIVE_PATH:
                self.lang = Lang(inbox=shared_inbox())
            else:
                raise v.BadRequest('隔离报价环境尚未配置语言服务，请注入临时语言库和假模型后再生成')
        return self.lang

    def day(self):
        return self.today() if callable(self.today) else self.today

    def _offer(self, db, product_id, offer_id, today):
        offers = [dict(r) for r in db.execute('SELECT * FROM offers WHERE product_id=?', (product_id,))]
        current = current_offer(offers)
        selected = get(db, 'offers', offer_id) if offer_id is not None else current
        if selected is None:
            return None
        if selected['product_id'] != product_id:
            raise v.BadRequest('产品与供应商报价不匹配，请选择该产品的报价')
        usable = quote_state(selected, today, current=bool(current and current['id'] == selected['id']))
        if not usable['usable_for_quote'] or selected['currency'] != 'CNY':
            if offer_id is None:
                return None
            raise v.BadRequest('产品报价不可用，请选择当前已确认、未过期且有有效期的人民币报价')
        return selected

    def _line(self, db, values, today, old=None):
        combined = dict(old or {}, **values)
        if 'qty' in values:
            combined['qty_confirmed'] = 1
        refresh = old is None or 'product_id' in values or 'offer_id' in values
        if refresh:
            pid = combined.get('product_id')
            if combined.get('offer_id') and not pid:
                raise v.BadRequest('引用供应商报价时请选择对应档案产品')
            product = get(db, 'products', pid) if pid is not None else None
            if product and (product['status'] == 'hidden' or db.execute('SELECT 1 FROM product_merges WHERE product_id=?', (pid,)).fetchone()):
                raise v.BadRequest('产品已隐藏或合并，请选择有效档案产品')
            if product and not values.get('name') and (not old or 'product_id' in values):
                combined['name'] = product['name']
            if not combined.get('name'):
                raise v.BadRequest('请填写产品名称')
            # Product change must not accidentally retain a different product's offer.
            oid = values.get('offer_id') if 'product_id' in values else combined.get('offer_id')
            offer = self._offer(db, pid, oid, today) if pid else None
            combined.update(offer_id=offer['id'] if offer else None,
                cost_unit_fen=offer['price_fen'] if offer else None, cost_currency='CNY',
                cost_valid_until=offer['valid_until'] if offer else None,
                cost_validity=validity(offer['valid_until'], today) if offer else None,
                lead_days_min=offer.get('lead_days_min') if offer else None,
                lead_days_max=offer.get('lead_days_max') if offer else None)
            for field in PACKING:
                combined[field] = product.get(field) if product else None
            if offer and 'unit' not in values:
                combined['unit'] = offer['unit']
        combined.pop('id', None)
        combined.pop('round_id', None)
        return combined

    def _freight(self, db, quote_id, today, *, latest=False):
        quote = get(db, 'freight_quotes', quote_id)
        group = [dict(r) for r in db.execute('SELECT * FROM freight_quotes WHERE forwarder_id=?', (quote['forwarder_id'],))
                 if route_key(dict(r)) == route_key(quote)]
        current = current_quote(group)
        if latest and current:
            quote = current
        if not quote_state(quote, today, current=bool(current and current['id'] == quote['id']))['usable_for_quote'] or quote['currency'] != 'CNY':
            raise v.BadRequest('运价不可用，请选择当前已确认、未过期且有有效期的人民币运价')
        # Store only calculation inputs and provenance; no raw notes or party names.
        result = {k: quote.get(k) for k in ('id','forwarder_id','mode','unit','price_fen','min_charge_fen',
            'currency','status','quoted_on','valid_until','destination_country','origin','destination_detail','service')}
        result['channel'] = quote.get('channel') or 'forwarder'
        return result

    @staticmethod
    def _check_route(round, quote):
        if not round.get('destination_country') or quote.get('destination_country') != round['destination_country']:
            raise v.BadRequest('运费目的国与本轮目的国不一致或未填写，请先核对目的国')
        if quote.get('mode') not in v.MODES:
            raise v.BadRequest('运输方式无效，请重新选择运价')

    @staticmethod
    def _freight_values(lines, quote, today):
        calc = calculate(lines, quote)
        if calc['amount_fen'] is not None and calc['amount_fen'] > v.MAX_FEN:
            raise v.BadRequest('运费总价超出允许范围，请核对数量与运价')
        calc['quote'] = quote
        return dict(freight_quote_id=quote['id'], party_id=quote['forwarder_id'],
            amount_fen=calc['amount_fen'], manual=0, valid_until=quote['valid_until'],
            validity=validity(quote['valid_until'], today),
            source_note='档案运价 #' + str(quote['id']), calc_json=json.dumps(calc, ensure_ascii=False))

    @staticmethod
    def _packing_reasons(lines, missing_lines):
        labels = {'carton_l_cm': '外箱长', 'carton_w_cm': '外箱宽', 'carton_h_cm': '外箱高',
                  'gross_kg': '毛重', 'units_per_carton': '每箱数量'}
        reasons = []
        if not lines:
            return ['报价轮没有产品行']
        by_number = {line_no: line for line_no, line in enumerate(lines, 1)}
        for line_no in missing_lines:
            line = by_number.get(line_no, {})
            missing = [label for field, label in labels.items() if line.get(field) is None]
            if line.get('qty') is None or type(line.get('qty')) is not int or line.get('qty', 0) <= 0:
                missing.append('采购数量')
            elif not line.get('qty_confirmed', 1):
                missing.append('已确认的采购数量')
            if not missing:
                missing.append('有效包装资料')
            reasons.append(f'第{line_no}条产品缺少' + '、'.join(missing))
        return reasons

    def _freight_rows(self, db):
        return [dict(row) for row in db.execute(
            'SELECT f.*, p.name AS forwarder_name FROM freight_quotes f '
            'JOIN parties p ON p.id=f.forwarder_id ORDER BY f.id')]

    def _current_freight_ids(self, rates):
        groups = {}
        for rate_row in rates:
            groups.setdefault(route_key(rate_row), []).append(rate_row)
        return {selected['id'] for selected in (current_quote(group) for group in groups.values()) if selected}

    def _freight_options(self, db, round_row, today, *, mode=None):
        lines = children(db, 'quote_lines', round_row['id'])
        rates = self._freight_rows(db)
        current_ids = self._current_freight_ids(rates)
        destination = round_row.get('destination_country')
        items = []
        for rate_row in rates:
            if rate_row['id'] not in current_ids:
                continue
            state = quote_state(rate_row, today, current=True)
            if not state['usable_for_quote']:
                continue
            if destination and rate_row['destination_country'] != destination:
                continue
            if mode and rate_row['mode'] != mode:
                continue
            quote = {key: rate_row.get(key) for key in ('id','forwarder_id','mode','unit','price_fen',
                'min_charge_fen','currency','status','quoted_on','valid_until','destination_country','channel')}
            calculation = calculate(lines, quote)
            reasons = self._packing_reasons(lines, calculation.get('missing_lines', []))
            raw_amount = calculation['amount_fen']
            amount_in_range = raw_amount is not None and 0 <= raw_amount <= v.MAX_FEN
            packing_calculable = amount_in_range
            if raw_amount is not None and not amount_in_range:
                reasons.append('计算金额超出允许范围')
            amount_cny = raw_amount if packing_calculable and rate_row['currency'] == 'CNY' else None
            result_reasons = list(reasons)
            if rate_row['currency'] != 'CNY':
                result_reasons.append('当前运费成本流程只支持人民币运价，无法换算为人民币金额')
            calc = {key: calculation.get(key) for key in
                    ('gross_kg','cbm','volumetric_kg','chargeable_kg','basis')}
            items.append(dict(
                id=rate_row['id'], freight_quote_id=rate_row['id'],
                forwarder_id=rate_row['forwarder_id'], forwarder_name=rate_row['forwarder_name'],
                origin=rate_row['origin'], destination_country=rate_row['destination_country'],
                destination_detail=rate_row['destination_detail'], destination_matches=True if destination else None,
                mode=rate_row['mode'], service=rate_row['service'], unit=rate_row['unit'],
                currency=rate_row['currency'], unit_price=money(rate_row['price_fen']),
                min_charge=money(rate_row['min_charge_fen']) if rate_row['min_charge_fen'] is not None else None,
                transit_days_min=rate_row['transit_days_min'], transit_days_max=rate_row['transit_days_max'],
                quoted_on=rate_row['quoted_on'], valid_until=rate_row['valid_until'],
                validity=state['validity'], remaining_validity_days=remaining_days(rate_row['valid_until'], today),
                channel=rate_row.get('channel') or 'forwarder',
                packing_calculable=packing_calculable, calculable=amount_cny is not None,
                calculation_reasons=result_reasons,
                amount_cny=money(amount_cny) if amount_cny is not None else None,
                amount_in_quote_currency=(money(raw_amount) if amount_in_range else None),
                billing_unit=rate_row['unit'], billing_quantity=calc['basis'],
                gross_kg=calc['gross_kg'], volumetric_kg=calc['volumetric_kg'],
                chargeable_kg=calc['chargeable_kg'], cbm=calc['cbm'],
                recommendations=[]))
        scopes = {}
        for item in items:
            scope = tuple(item.get(key) or '' for key in ('destination_country','destination_detail','mode','origin','service'))
            scopes.setdefault(scope, []).append(item)

        def speed_key(item):
            low = item['transit_days_min'] if item['transit_days_min'] is not None else item['transit_days_max']
            high = item['transit_days_max'] if item['transit_days_max'] is not None else low
            return low, high

        for scoped_items in scopes.values():
            priced = [item for item in scoped_items if item['amount_cny'] is not None]
            cheapest = min((int(Decimal(item['amount_cny']) * 100) for item in priced), default=None)
            for item in priced:
                if int(Decimal(item['amount_cny']) * 100) == cheapest:
                    item['recommendations'].append('cheapest')
            fastest = [item for item in scoped_items if item['transit_days_min'] is not None
                       or item['transit_days_max'] is not None]
            if fastest:
                best_speed = min(speed_key(item) for item in fastest)
                for item in fastest:
                    if speed_key(item) == best_speed:
                        item['recommendations'].append('fastest')
            balanced = [item for item in priced if cheapest is not None
                        and (item['transit_days_min'] is not None or item['transit_days_max'] is not None)
                        and int(Decimal(item['amount_cny']) * 100) * 10 <= cheapest * 11]
            if balanced:
                best_speed = min(speed_key(item) for item in balanced)
                for item in balanced:
                    if speed_key(item) == best_speed:
                        item['recommendations'].append('balanced')
        items.sort(key=lambda item: (item['destination_country'], item['mode'], item['forwarder_name'].casefold(),
                                     item['service'], item['unit'], item['id']))
        return dict(destination_country=destination, destination_unspecified=not bool(destination),
                    destination_notice=('报价轮未填写目的国，当前列出所有目的国运价，请核对目的地。'
                                        if not destination else None),
                    mode_filter=mode, items=items)

    def _freight_compare(self, db, params, today):
        rates = self._freight_rows(db)
        current_ids = self._current_freight_ids(rates)
        items = []
        for rate_row in rates:
            if rate_row['id'] not in current_ids:
                continue
            state = quote_state(rate_row, today, current=True)
            if not state['usable_for_quote']:
                continue
            if params.get('destination_country') and rate_row['destination_country'] != params['destination_country']:
                continue
            if params.get('mode') and rate_row['mode'] != params['mode']:
                continue
            items.append(dict(id=rate_row['id'], forwarder_id=rate_row['forwarder_id'],
                forwarder_name=rate_row['forwarder_name'], origin=rate_row['origin'],
                destination_country=rate_row['destination_country'], destination_detail=rate_row['destination_detail'],
                mode=rate_row['mode'], service=rate_row['service'], unit=rate_row['unit'],
                unit_price=money(rate_row['price_fen']), currency=rate_row['currency'],
                min_charge=money(rate_row['min_charge_fen']) if rate_row['min_charge_fen'] is not None else None,
                transit_days_min=rate_row['transit_days_min'], transit_days_max=rate_row['transit_days_max'],
                quoted_on=rate_row['quoted_on'], valid_until=rate_row['valid_until'],
                validity=state['validity'], remaining_validity_days=remaining_days(rate_row['valid_until'], today),
                channel=rate_row.get('channel', 'forwarder')))
        items.sort(key=lambda item: (item['destination_country'], item['mode'], item['forwarder_name'].casefold(),
                                     item['origin'], item['service'], item['unit'], item['id']))
        total = len(items)
        page = params.get('page', 1)
        page_size = params.get('page_size', 30)
        start = (page - 1) * page_size
        return dict(destination_country=params.get('destination_country'), mode=params.get('mode'),
                    items=items[start:start + page_size], total=total, page=page, page_size=page_size)

    def _refresh_freight(self, db, rid, who, today, *, quantity_changed=False):
        # Quantity/line changes recompute against the frozen rate and frozen
        # packaging. Source changes take effect only through explicit recalc.
        lines = children(db, 'quote_lines', rid)
        for cost in children(db, 'quote_costs', rid):
            if cost['kind'] == 'freight' and not cost['manual']:
                prior = json.loads(cost['calc_json'])
                values = self._freight_values(lines, prior['quote'], today)
                calc = json.loads(values['calc_json'])
                calc['segment'] = prior.get('segment')
                values['calc_json'] = json.dumps(calc, ensure_ascii=False)
                update(db, 'quote_costs', cost['id'], values, who)
            elif cost['manual'] and quantity_changed:
                calc = json.loads(cost['calc_json'] or '{}')
                if calc.get('unit_price_fen') is not None and calc.get('quantity_line_id'):
                    line = next((line for line in lines if line['id'] == calc['quantity_line_id']), None)
                    if line and line.get('qty_confirmed', 1) and calc.get('unit') and calc['unit'] == line.get('unit'):
                        calc['quantity'] = line['qty']
                        amount = calc['unit_price_fen'] * line['qty']
                        if amount > v.MAX_FEN:
                            raise v.BadRequest('费用总价超出范围，请核对数量和单价')
                        update(db, 'quote_costs', cost['id'], dict(amount_fen=amount, calc_json=json.dumps(calc, ensure_ascii=False)), who)
                        continue
                calc['needs_review'] = True
                update(db, 'quote_costs', cost['id'], dict(calc_json=json.dumps(calc, ensure_ascii=False)), who)

    def _cost_sources(self, db, today):
        rows = []
        has_extract = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='extraction_suggestions'").fetchone()
        for product in db.execute("SELECT * FROM products WHERE status!='hidden' ORDER BY id"):
            offer = self._offer(db, product['id'], None, today)
            if not offer:
                continue
            party_id = product['supplier_id']
            source = db.execute("SELECT party_id,conversation_key FROM extraction_suggestions WHERE kind='offer' AND status='accepted' AND accepted_record_id=?", (offer['id'],)).fetchone() if has_extract else None
            if source and source['party_id']:
                party_id = source['party_id']
            if not party_id:
                continue
            party = get(db, 'parties', party_id)
            rows.append(dict(id=offer['id'], product_id=product['id'], name=product['name'], party_id=party_id,
                party_name=party['name'], unit=offer['unit'], unit_price=money(offer['price_fen']),
                currency=offer['currency'], valid_until=offer['valid_until'], source_ref=offer['source_ref'],
                conversation_key=source['conversation_key'] if source else None))
        return rows

    def _manual_cost(self, db, round, values, today):
        calc = {}
        for key in ('quantity_line_id','quantity','unit','destination_country','mode','segment'):
            if key in values:
                calc[key] = values.pop(key)
        unit_price = values.pop('unit_price_fen', None)
        source_id = values.pop('source_offer_id', None)
        if values['kind'] in ('vendor','domestic') and not values.get('party_id'):
            raise v.BadRequest('请选择具体配套或拖车单位，再确认费用')
        if source_id:
            source = next((item for item in self._cost_sources(db, today) if item['id'] == source_id), None)
            if not source or source['party_id'] != values.get('party_id'):
                raise v.BadRequest('询价来源与所选单位不一致，请重新核对')
            if any(line['product_id'] == source['product_id'] for line in children(db, 'quote_lines', round['id'])):
                raise v.BadRequest('该配套产品已计入产品成本，不能重复计入配套费用')
            if values.get('amount_fen') is not None or unit_price is not None:
                raise v.BadRequest('选择档案询价来源后，单价取自该来源，请勿重复填写总价或单价')
            calc['source_offer'] = source
            unit_price = v.money_to_fen(source['unit_price'])
            values.update(valid_until=source['valid_until'], validity=validity(source['valid_until'], today))
            calc['unit'] = source['unit']
        if values.get('party_id'):
            party = get(db, 'parties', values['party_id'])
            if party['status'] != 'active' or values['kind'] == 'vendor' and party['kind'] != 'vendor':
                raise v.BadRequest('配套费用请选择有效配套单位，其他费用请选择有效单位')
            if values['kind'] in ('domestic','freight') and party['kind'] not in ('vendor','forwarder'):
                raise v.BadRequest('运输费用请选择具体货代或配套运输单位')
            calc['party'] = {key: party.get(key) for key in ('id','name','kind')}
        if unit_price is not None:
            line_id = calc.get('quantity_line_id')
            if line_id:
                line = get(db, 'quote_lines', line_id)
                if line['round_id'] != round['id'] or not line.get('qty_confirmed', 1):
                    raise v.BadRequest('计价数量必须关联本轮已确认的产品行')
                if calc.get('unit') and calc['unit'] != line.get('unit'):
                    raise v.BadRequest('计价单位与产品行不同，请人工确认数量换算后使用独立数量')
                calc['unit'] = calc.get('unit') or line.get('unit')
                if not calc['unit']:
                    raise v.BadRequest('关联数量计价须明确单位，请先补齐产品行单位')
                calc['quantity'] = line['qty']
            if not calc.get('quantity'):
                raise v.BadRequest('按单价计费须选择数量行或填写已确认的数量')
            calc['unit_price_fen'] = unit_price
            values['amount_fen'] = unit_price * calc['quantity']
            if values['amount_fen'] > v.MAX_FEN:
                raise v.BadRequest('费用总价超出范围，请核对数量和单价')
        if values['kind'] == 'freight':
            self._check_route(round, calc)
        calc.update(source_note=values['source_note'], confirmed_by=None, needs_review=False)
        values['calc_json'] = json.dumps(calc, ensure_ascii=False)
        return values

    @staticmethod
    def _check_accessory(db, rid, line):
        if line.get('product_id'):
            for cost in children(db, 'quote_costs', rid):
                source = json.loads(cost['calc_json'] or '{}').get('source_offer', {})
                if source.get('product_id') == line['product_id']:
                    raise v.BadRequest('该产品已计入配套费用，不能再计入产品成本；请保留一种计价方式')

    def write(self, operation, row_id, fields, who, child_id=None):
        fields, operation_key = operations.split(fields)
        if operation == 'draft':
            v.whitelist(fields, ('kind', 'lang'))
            values = dict(kind=v.choice(fields.get('kind'), ('quote', 'pi')),
                          lang=v.choice(fields.get('lang', 'zh'), ('zh', 'en')))
        else:
            values = validation.fields(operation, fields)
        if operation == 'native':
            return self._native(row_id, values, who)
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec='microseconds')
        today = self.day()
        try:
            with self.store.write() as db:
                return operations.once(db, f'quotes:{who}:{operation}:{row_id}:{child_id}', operation_key,
                    fields, lambda: self._write(db, operation, row_id, values, who, stamp, today, child_id))
        except sqlite3.IntegrityError:
            raise v.Conflict('记录重复或关联、字段约束不满足') from None

    def _write(self, db, op, rid, values, who, stamp, today, cid):
        response_extra = {}
        if op == 'create':
            deal = get(db, 'deals', rid)
            last = db.execute('SELECT * FROM quote_rounds WHERE deal_id=? ORDER BY round_no DESC LIMIT 1', (rid,)).fetchone()
            fx = read_fx(self.fx_path)
            initial = dict(deal_id=rid, round_no=last['round_no'] + 1 if last else 1,
                           created_at=stamp, updated_at=stamp, currency='USD', incoterm='DDP')
            if last:
                initial.update({k: last[k] for k in ('currency','incoterm','destination_country','target_margin_bp','notes')})
                initial['copied_from_round_id'] = last['id']
                # Carry explicitly supplied non-USD rates; USD uses the current
                # local cache and remains blocked if it cannot be read.
                if last['currency'] != 'USD':
                    initial.update(fx_x10000=last['fx_x10000'], fx_source=last['fx_source'])
            elif deal.get('customer_party_id'):
                initial['destination_country'] = get(db, 'parties', deal['customer_party_id'])['country']
            if not last and operations.context(db, rid):
                initial['destination_country'] = operations.context(db, rid)
            if initial['currency'] == 'USD':
                initial.update(fx_x10000=int(Decimal(fx['usd_cny']) * 10000) if fx['usd_cny'] else None,
                               fx_source=(fx['source'] + (' @ ' + str(fx['as_of']) if fx['as_of'] is not None else '')) if fx['source'] else None)
            new_id = insert(db, 'quote_rounds', initial, who)
            if last:
                line_ids = {}
                for table in ('quote_lines','quote_costs'):
                    for row in children(db, table, last['id']):
                        prior_id = row.pop('id')
                        row['round_id'] = new_id
                        if table == 'quote_costs' and row.get('calc_json'):
                            calc = json.loads(row['calc_json'])
                            if calc.get('quantity_line_id'):
                                calc['quantity_line_id'] = line_ids.get(calc['quantity_line_id'])
                                if calc['quantity_line_id'] is None:
                                    calc['needs_review'] = True
                            row['calc_json'] = json.dumps(calc, ensure_ascii=False)
                        copied_id = insert(db, table, row, who, 'copy')
                        if table == 'quote_lines':
                            line_ids[prior_id] = copied_id
                prior_state = self.state.get(self._state_key(dict(last)))
                if prior_state.get('terms'):
                    terms = dict(prior_state['terms'], confirmed=False)
                    self.state.save(self._state_key(get(db, 'quote_rounds', new_id)), dict(terms=terms), who, 'copy', stamp)
                if last['status'] == 'sent':
                    update(db, 'quote_rounds', last['id'], dict(status='superseded', updated_at=stamp), who)
                    self.deals.quote_event(db, rid, f"报价第 {last['round_no']} 轮已被新轮替代", who, stamp)
            else:
                for item in db.execute('SELECT * FROM deal_items WHERE deal_id=? ORDER BY id', (rid,)).fetchall():
                    line = self._line(db, dict(product_id=item['product_id'], name=item['name'], qty=item['qty'] or 1), today)
                    line.update(deal_item_id=item['id'], qty_confirmed=int(item['qty'] is not None))
                    insert(db, 'quote_lines', dict(line, round_id=new_id), who)
            self.deals.quote_event(db, rid, f"创建报价第 {initial['round_no']} 轮", who, stamp)
            return dict(ok=True, id=new_id)
        round = get(db, 'quote_rounds', rid)
        if op == 'draft':
            document = self._pi(db, round) if values['kind'] == 'pi' else self._document(db, round, values['lang'])[0]
            if not document['ready']:
                reasons = [problem['message'] for problem in document.get('blocking', [])]
                reasons.extend(document.get('missing', []))
                raise v.Conflict('暂不能生成草稿：' + '；'.join(reasons))
            result = operations.save_draft(db, round['deal_id'], values['kind'], document['text'], who, stamp, quote_id=rid)
            deal = get(db, 'deals', round['deal_id'])
            latest = db.execute('SELECT MAX(round_no) FROM quote_rounds WHERE deal_id=?', (round['deal_id'],)).fetchone()[0]
            if deal['status'] == 'open' and round['round_no'] == latest:
                amount = sum(line['qty'] * line['sell_unit_minor'] for line in children(db, 'quote_lines', rid))
                if amount > v.MAX_FEN:
                    raise v.BadRequest('本轮报价总额超出案子金额范围，请核对数量和售价')
                self.deals._write(db, 'update', round['deal_id'], dict(value_fen=amount, currency=round['currency']), who, stamp, None)
            self.deals.quote_event(db, round['deal_id'], 'PI 已存演练草稿，未发送' if values['kind'] == 'pi' else '报价单已存演练草稿，未发送', who, stamp)
            return result
        if op not in ('negotiation','status') and round['status'] != 'draft' and not (op == 'update' and set(values) <= {'notes'}):
            raise v.Conflict(LOCKED)
        if op in ('terms','review'):
            result = self._document_write(db, round, op, values, who, stamp)
            if not result.get('reused'):
                self.deals.quote_event(db, round['deal_id'], '确认本轮付款与交付条件' if op == 'terms' else '本轮报价文本已审阅', who, stamp)
            return result
        if op == 'update':
            if values.get('currency', round['currency']) != round['currency']:
                if 'fx_x10000' not in values:
                    values.update(fx_x10000=None, fx_source=None)
                # A currency label cannot reinterpret an old numeric sales price.
                for line in children(db, 'quote_lines', rid):
                    update(db, 'quote_lines', line['id'], dict(sell_unit_minor=None), who)
            if 'fx_x10000' in values:
                values['fx_source'] = values.get('fx_source') or ('人工填写' if values['fx_x10000'] else None)
            update(db, 'quote_rounds', rid, values, who)
        elif op in ('line','line_update','line_remove','cost_remove','cost_recalc','cost_review'):
            old = None
            if op != 'line':
                table = 'quote_lines' if op.startswith('line') else 'quote_costs'
                old = get(db, table, cid)
                if old['round_id'] != rid:
                    raise v.Missing('未找到该报价轮内的记录')
            if op.endswith('_remove'):
                db.execute('DELETE FROM ' + table + ' WHERE id=?', (cid,))
                audit(db, who, 'remove', table, cid, ('id',))
            elif op == 'line':
                line = self._line(db, values, today)
                self._check_accessory(db, rid, line)
                cid = insert(db, 'quote_lines', dict(line, round_id=rid), who)
            elif op == 'line_update':
                line = self._line(db, values, today, old)
                self._check_accessory(db, rid, line)
                update(db, 'quote_lines', cid, line, who)
            elif op == 'cost_review':
                if values.get('confirm') != 1 or not old['manual']:
                    raise v.BadRequest('请核对人工总费用后明确确认')
                calc = json.loads(old['calc_json'] or '{}')
                calc.update(needs_review=False, reviewed_by=who, reviewed_at=stamp)
                update(db, 'quote_costs', cid, dict(calc_json=json.dumps(calc, ensure_ascii=False)), who)
            elif op == 'cost_recalc':
                if old['manual'] or old['kind'] != 'freight':
                    raise v.BadRequest('人工费用不能自动重算，请移除后重新填写')
                quote = self._freight(db, old['freight_quote_id'], today, latest=True)
                self._check_route(round, quote)
                for line in children(db, 'quote_lines', rid):
                    if line['product_id']:
                        product = get(db, 'products', line['product_id'])
                        update(db, 'quote_lines', line['id'], {k: product[k] for k in PACKING}, who, 'recalc')
                update(db, 'quote_costs', cid, self._freight_values(children(db, 'quote_lines', rid), quote, today), who, 'recalc')
            if op.startswith('line') or op == 'cost_recalc':
                changed = op in ('line','line_remove') or op == 'line_update' and any(values.get(k, old.get(k)) != old.get(k) for k in ('qty','unit','product_id'))
                self._refresh_freight(db, rid, who, today, quantity_changed=changed)
        elif op == 'cost':
            if values.get('party_id'):
                get(db, 'parties', values['party_id'])
            if not values.get('manual'):
                quote = self._freight(db, values['freight_quote_id'], today)
                self._check_route(round, quote)
                if db.execute("SELECT 1 FROM quote_costs WHERE round_id=? AND kind='freight' AND manual=0", (rid,)).fetchone():
                    raise v.Conflict('已有运费方案，请在比价中明确选择替换或追加运输段')
                if values.get('party_id') and values['party_id'] != quote['forwarder_id']:
                    raise v.BadRequest('费用对象与运价货代不匹配')
                values.update(self._freight_values(children(db, 'quote_lines', rid), quote, today))
            else:
                values['validity'] = validity(values['valid_until'], today)
                values = self._manual_cost(db, round, values, today)
            cid = insert(db, 'quote_costs', dict(values, round_id=rid), who)
        elif op == 'freight_choose':
            key = values.get('request_key')
            fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if key:
                recorded = db.execute("SELECT note FROM quote_negotiations WHERE round_id=? AND instr(note,?)>0", (rid, '〔请求:' + key + ';')).fetchone()
                if recorded:
                    match = re.search(r'〔请求:[A-Za-z0-9-]+;摘要:([a-f0-9]{64});费用:(\d+)〕', recorded['note'])
                    if not match or match[1] != fingerprint:
                        raise v.Conflict('同一请求编号的内容已改变，请刷新后重新确认')
                    return dict(ok=True, id=int(match[2]), cost_id=int(match[2]), reused=True)
            quote_id = values['freight_quote_id']
            get(db, 'freight_quotes', quote_id)
            options = self._freight_options(db, round, today)['items']
            option = next((item for item in options if item['freight_quote_id'] == quote_id), None)
            if option is None:
                raise v.Conflict('该运价不是当前目的国下有效的运价，请刷新比价结果')
            quote = self._freight(db, quote_id, today)
            self._check_route(round, quote)
            if not option['calculable']:
                raise v.BadRequest('包装或金额资料不完整，请补齐后再选择')
            selection = values.get('selection_mode')
            replacement = None
            if selection == 'replace':
                if not values.get('replace_cost_id') or not key:
                    raise v.BadRequest('替换须指定原费用和请求编号，再人工确认')
                replacement = get(db, 'quote_costs', values['replace_cost_id'])
                if replacement['round_id'] != rid or replacement['kind'] != 'freight' or replacement['manual']:
                    raise v.BadRequest('只能替换本轮的自动运输方案')
                prior = json.loads(replacement['calc_json'])['quote']
                if prior['mode'] != quote['mode']:
                    raise v.BadRequest('运输方式不同，请核对是替换方案还是追加运输段')
            elif selection == 'append':
                if not key or not values.get('segment') or not values.get('reason'):
                    raise v.BadRequest('追加运输段须填写运输段、选择原因和请求编号')
            elif db.execute("SELECT 1 FROM quote_costs WHERE round_id=? AND kind='freight' AND manual=0 AND freight_quote_id!=?", (rid, quote_id)).fetchone():
                raise v.Conflict('已有运费方案，请明确选择替换或追加运输段')
            existing_cost = db.execute(
                "SELECT id FROM quote_costs WHERE round_id=? AND kind='freight' AND manual=0 "
                'AND freight_quote_id=? ORDER BY id LIMIT 1', (rid, quote_id)).fetchone()
            if existing_cost and selection == 'append':
                prior_cost = get(db, 'quote_costs', existing_cost['id'])
                prior_segment = json.loads(prior_cost['calc_json']).get('segment') or '整票运输方案'
                if prior_segment != values.get('segment'):
                    raise v.BadRequest('同一运价已覆盖已有运输段，不能直接作为另一段追加；请先向货代核对覆盖范围并取得对应运价')
            if existing_cost:
                cid = existing_cost['id']
            else:
                cost_values = self._freight_values(children(db, 'quote_lines', rid), quote, today)
                calc = json.loads(cost_values['calc_json'])
                calc['segment'] = values.get('segment') or '整票运输方案'
                cost_values['calc_json'] = json.dumps(calc, ensure_ascii=False)
                cid = insert(db, 'quote_costs', dict(kind='freight', **cost_values, round_id=rid), who)
            if replacement and replacement['id'] != cid:
                db.execute('DELETE FROM quote_costs WHERE id=?', (replacement['id'],))
                audit(db, who, 'replace', 'quote_costs', replacement['id'], ('id',))
            party = get(db, 'parties', quote['forwarder_id'])
            reason_labels = {'cheapest': '最便宜', 'fastest': '最快', 'other': '其他'}
            reason = reason_labels[values['reason_type']]
            if values.get('reason'):
                reason += '；' + values['reason']
            channel = quote.get('channel') or 'forwarder'
            channel_label = FREIGHT_CHANNEL_LABELS.get(channel, FREIGHT_CHANNEL_LABELS['other'])
            note = f"选择了 {party['name']}（来源 {channel_label}），原因：{reason}（运价 #{quote_id}）"
            if selection:
                note += '；' + ('替换运输方案' if selection == 'replace' else '追加运输段：' + values['segment'])
            if key:
                note += f'〔请求:{key};摘要:{fingerprint};费用:{cid}〕'
            marker = f'（运价 #{quote_id}）'
            recorded = db.execute(
                "SELECT 1 FROM quote_negotiations WHERE round_id=? AND side='forwarder' "
                'AND instr(note,?)>0 LIMIT 1', (rid, marker)).fetchone() is not None
            if key or not recorded or not existing_cost:
                insert(db, 'quote_negotiations', dict(round_id=rid, side='forwarder',
                    party_id=quote['forwarder_id'], note=note, price_minor=None, currency=None,
                    at=stamp, who=who), who)
                self.deals.quote_event(db, round['deal_id'],
                    f"报价第 {round['round_no']} 轮{note}"[:500], who, stamp)
            response_extra = dict(cost_id=cid, freight_quote_id=quote_id,
                                  reused=bool(existing_cost and recorded))
        elif op == 'negotiation':
            if values.get('party_id'):
                party = get(db, 'parties', values['party_id'])
                if party['kind'] != values['side']:
                    raise v.BadRequest('协商角色与具体单位不一致，请重新选择')
            if values.get('price_minor') is not None:
                values.setdefault('currency', round['currency'])
            cid = insert(db, 'quote_negotiations', dict(values, round_id=rid, at=stamp, who=who), who)
            self.deals.quote_event(db, round['deal_id'], f"报价第 {round['round_no']} 轮记录还价与回应", who, stamp)
        elif op == 'status':
            action = values['action']
            expected = 'draft' if action == 'send' else 'sent'
            if round['status'] != expected:
                raise v.Conflict('当前报价状态不能执行此操作，请核对状态或新建下一轮')
            if action == 'send':
                if not values.get('confirm'):
                    raise v.BadRequest('请确认报价后再标记发出（confirm=1）')
                check = compute(round, children(db, 'quote_lines', rid), children(db, 'quote_costs', rid), today)
                state = self.state.get(self._state_key(round))
                check['blocking'].extend(self._term_issues(state.get('terms', {})))
                if check['blocking']:
                    raise v.Conflict('报价有阻断问题，请先处理：' + '；'.join(dict.fromkeys(p['message'] for p in check['blocking'])))
                changes = dict(status='sent', sent_at=stamp)
            else:
                changes = dict(status='accepted' if action == 'accept' else 'rejected')
            update(db, 'quote_rounds', rid, changes, who)
            label = {'send':'已发出','accept':'已接受','reject':'已拒绝'}[action]
            self.deals.quote_event(db, round['deal_id'], f"报价第 {round['round_no']} 轮{label}", who, stamp, accepted=action == 'accept')
        else:
            raise v.BadRequest('操作无效')
        update(db, 'quote_rounds', rid, dict(updated_at=stamp), who)
        return dict(ok=True, id=cid if cid is not None else rid, **response_extra)

    def _view(self, db, round, today, *, detail=False):
        lines = children(db, 'quote_lines', round['id'])
        costs = children(db, 'quote_costs', round['id'])
        result = compute(round, lines, costs, today)
        state = self.state.get(self._state_key(round))
        result['blocking'].extend(self._term_issues(state.get('terms', {})))
        if not detail:
            return {**{k: round[k] for k in ('id','round_no','status','currency','incoterm','sent_at')},
                    'totals': result['totals'], 'blocking': len(result['blocking']), 'warnings': len(result['warnings'])}
        view = dict(round, **result)
        view['terms'] = state.get('terms', {})
        doc, _, _, _ = self._document(db, round)
        view['review'] = state.get('review') if state.get('review', {}).get('source_hash') == doc['source_hash'] else None
        view['review_history'] = state.get('reviews', [state['review']] if state.get('review') else [])
        if view['review'] and view['review']['lang'] == 'native':
            try:
                native = self._native_view(doc, state)
                if view['review']['text'] != native['text']:
                    view['review'] = None
            except v.BadRequest:
                view['review'] = None
        view['cost_sources'] = self._cost_sources(db, today)
        view['parties'] = [dict(row) for row in db.execute("SELECT id,name,kind FROM parties WHERE status='active' ORDER BY name,id")]
        view['customer_conversations'] = self._customer_conversations(db, get(db, 'deals', round['deal_id']))
        view['fx_rate'] = rate(view.pop('fx_x10000'))
        view['target_margin'] = money(view.pop('target_margin_bp')) + '%'
        view['fx'] = read_fx(self.fx_path)
        view['lines'] = [{**{k: v for k, v in l.items() if k not in ('cost_unit_fen','sell_unit_minor')},
            'cost_unit': money(l['cost_unit_fen']), 'sell_unit_price': money(l['sell_unit_minor'])} for l in lines]
        view['costs'] = [{**{k: v for k, v in c.items() if k not in ('amount_fen','calc_json')},
            'amount': money(c['amount_fen']), 'calc': public_calc(json.loads(c['calc_json'])) if c['calc_json'] else None} for c in costs]
        view['negotiations'] = [{**{k: v for k, v in n.items() if k != 'price_minor'}, 'price': money(n['price_minor'])}
                                for n in children(db, 'quote_negotiations', round['id'])]
        for line in view['per_line']:
            line['min_price_for_target'] = money(line['min_price_for_target_minor'])
        view['totals']['revenue'] = money(result['totals']['revenue_minor'])
        return view

    def read(self, resource, params=None, row_id=None):
        params = params or {}
        if resource == 'fx':
            return read_fx(self.fx_path)
        today = self.day()
        with self.store.read() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if resource == 'list':
                if 'deals' not in tables:
                    raise v.Missing('未找到该案子')
                get(db, 'deals', row_id)
                rounds = db.execute('SELECT * FROM quote_rounds WHERE deal_id=? ORDER BY round_no', (row_id,)) if 'quote_rounds' in tables else ()
                return dict(items=[self._view(db, dict(r), today) for r in rounds])
            if resource == 'freight_compare':
                return self._freight_compare(db, params, today)
            if 'quote_rounds' not in tables:
                raise v.Missing('未找到该报价轮')
            round = get(db, 'quote_rounds', row_id)
            if resource == 'freight_options':
                return self._freight_options(db, round, today, mode=params.get('mode'))
            if resource == 'text':
                lang = params.get('lang', 'en')
                doc, state, _, _ = self._document(db, round, lang)
                return self._native_view(doc, state) if lang == 'native' else doc
            if resource == 'pi':
                return self._pi(db, round)
            return self._view(db, round, today, detail=True)
