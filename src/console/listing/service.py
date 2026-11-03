"""Owner-reviewed listing drafting, pricing, compliance, and external-only export."""
from collections import defaultdict
from datetime import date
import csv
from io import StringIO
import json
import re
import time

from console.archive.service import get
from console.archive.store import ARCHIVE_PATH, Store, insert, update
from console.archive.validation import BadRequest, Conflict, Missing
from console.inbox import ai as inbox_ai
from console.kb.hub import default_hub
from console.quotes import fx as quote_fx
from console.roles import ai as role_ai
from console.roles.common import dumps, fingerprint, safe, stamp
from console.roles.storage import RateLimited, reserve, save_run, serialized

from . import compliance, drafting, pricing, validation
from .config import Config

DEFAULT_RUNNER = role_ai.DEFAULT_RUNNER
_NUMBER = re.compile(r'(?<![A-Za-z])\d+(?:[.,]\d+)?')


def _j(value, default):
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def _fmt(value, unit=''):
    if value is None:
        return ''
    number = format(float(value), '.6f').rstrip('0').rstrip('.')
    return number + unit


def _dimensions(row, prefix):
    keys = (prefix + '_l_cm', prefix + '_w_cm', prefix + '_h_cm')
    if not all(row.get(key) is not None for key in keys):
        return ''
    return ' × '.join(_fmt(row[key]) for key in keys) + ' cm'


def _day(source):
    value = source() if callable(source) else source
    return value or date.today()


def _listing_values(row):
    result = dict(row)
    for field, default in (
        ('keywords_json', []), ('attributes_json', {}), ('price_tiers_json', []),
        ('packaging_json', {}), ('selling_points_json', []), ('image_refs_json', []),
        ('compliance_json', {}), ('sources_json', []), ('internal_pricing_json', {}),
    ):
        result[field.removesuffix('_json')] = _j(result.pop(field), default)
    result['generation_source'] = result.pop('generation_source')
    result['source'] = result['generation_source']
    return result


class Listing:
    def __init__(self, path=ARCHIVE_PATH, *, store=None, runner=DEFAULT_RUNNER, hub=None,
                 clock=None, today=None, fx_reader=quote_fx.read_fx, config=None,
                 brand_terms=(), media_lookup=None):
        self.store = Store(store.path, connection=store.connection, target_version=10) if store else Store(path, target_version=10)
        self.clock = clock or time.time
        self.today = today
        self.fx_reader = fx_reader
        self.config = config or Config()
        self.runner = inbox_ai.official_runner if runner is DEFAULT_RUNNER else runner
        self.hub = hub or default_hub()
        self.brand_terms = tuple(brand_terms or compliance.DEFAULT_BRAND_TERMS)
        # Existing company_media records are conversation attachments, not a
        # product photo catalog. A host may inject a product-safe resolver.
        self.media_lookup = media_lookup or (lambda product_id, ref: False)

    def day(self):
        return _day(self.today)

    def _product_snapshot(self, product_id, market=''):
        with self.store.read() as db:
            product = get(db, 'products', product_id)
            if product['status'] != 'confirmed':
                raise Conflict('只能从已确认产品资料起草发品内容')
            if db.execute('SELECT 1 FROM product_merges WHERE product_id=?', (product_id,)).fetchone():
                raise Conflict('产品已合并，请使用合并后的产品档案')
            supplier_terms = []
            # These identities are only used for in-memory leak checks; none
            # is sent to the model, saved into a draft, or returned by the API.
            for party in db.execute("SELECT name,aliases_json FROM parties WHERE kind IN ('supplier','customer')"):
                supplier_terms.append(party['name'])
                supplier_terms.extend(_j(party['aliases_json'] or '[]', []))
            offers = [dict(row) for row in db.execute('SELECT * FROM offers WHERE product_id=? ORDER BY id', (product_id,))]
            sources = [dict(row) for row in db.execute('SELECT id,kind FROM product_sources WHERE product_id=? ORDER BY id',
                                                       (product_id,))]
            freight = []
            if market and 'freight_quotes' in {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")} :
                freight = [dict(row) for row in db.execute(
                    'SELECT * FROM freight_quotes WHERE lower(trim(destination_country))=lower(trim(?)) ORDER BY id',
                    (market,))]
        return product, offers, sources, freight, tuple(t for t in supplier_terms if isinstance(t, str) and t.strip())

    @staticmethod
    def _fact_snapshot(product, offers, today):
        current = pricing.current_usable_offer(offers, today)
        facts = {
            'name': safe(product.get('name'), 200),
            'model': safe(product.get('model'), 200) if product.get('model') else '',
            'category': safe(product.get('category'), 100) if product.get('category') else '',
            'wattage': _fmt(product.get('wattage'), ' W') if product.get('wattage') is not None else '',
            'product_dimensions': _dimensions(product, 'product'),
            'carton_dimensions': _dimensions(product, 'carton'),
            'gross_weight': _fmt(product.get('gross_kg'), ' kg') if product.get('gross_kg') is not None else '',
            'net_weight': _fmt(product.get('net_kg'), ' kg') if product.get('net_kg') is not None else '',
            'units_per_carton': str(product['units_per_carton']) if product.get('units_per_carton') is not None else '',
        }
        if current:
            facts['moq'] = str(current['moq_units']) if current.get('moq_units') is not None else ''
            facts['unit'] = safe(current.get('unit'), 40) if current.get('unit') else ''
            low, high = current.get('lead_days_min'), current.get('lead_days_max')
            if low is not None or high is not None:
                facts['lead_time'] = (str(low) if low is not None else str(high)) + (
                    ('–' + str(high)) if low is not None and high is not None and low != high else '') + ' days after order confirmation'
            else:
                facts['lead_time'] = ''
        else:
            facts.update(moq='', unit='', lead_time='')
        attributes = {
            'Model': facts['model'],
            'Category': facts['category'],
            'Power': facts['wattage'],
            'Product size': facts['product_dimensions'],
            'Carton size': facts['carton_dimensions'],
            'Gross weight': facts['gross_weight'],
            'Units per carton': facts['units_per_carton'],
            'MOQ': facts['moq'],
            'Lead time': facts['lead_time'],
            'Voltage': '',
            'Light source': '',
            'Ingress protection': '',
            'Material': '',
            'Certifications': '',
        }
        facts['attributes'] = {key: value if value else '' for key, value in attributes.items()}
        return facts, current

    def _knowledge(self, facts, supplier_terms):
        query = ' '.join(value for value in (facts.get('model'), facts.get('name'), facts.get('category'),
                                             'technical specifications certification voltage power') if value)
        if not query:
            return []
        try:
            result = self.hub.search(query, purpose='product_facts', top_k=self.config.knowledge_limit,
                                     families=('technical',))
        except Exception:
            return []
        items = []
        for item in result.get('items', ()) if isinstance(result, dict) else ():
            if (item.get('family') != 'technical' or item.get('source_type_label') != '公开技术参考'
                    or item.get('confidential') or item.get('is_cost_price')
                    or not isinstance(item.get('source_id'), str) or not isinstance(item.get('snippet'), str)):
                continue
            snippet = safe(item['snippet'], 300)
            if not snippet or any(term.casefold() in snippet.casefold() for term in supplier_terms if len(term.strip()) >= 3):
                continue
            items.append(dict(source_id=item['source_id'], snippet=snippet))
            if len(items) >= self.config.knowledge_limit:
                break
        return items

    @staticmethod
    def _remove_supplier_terms(facts, supplier_terms):
        result = {}
        for key, value in facts.items():
            if isinstance(value, dict):
                result[key] = Listing._remove_supplier_terms(value, supplier_terms)
            elif isinstance(value, str):
                cleaned = value
                for term in supplier_terms:
                    if len(term.strip()) >= 3:
                        cleaned = re.sub(re.escape(term.strip()), '', cleaned, flags=re.I).strip()
                result[key] = cleaned
            else:
                result[key] = value
        return result

    @staticmethod
    def _facts_for_prompt(facts):
        # Deliberately only product-specific technical fields. Never include
        # free-form archive descriptions, party names, costs, or source notes.
        return {key: value for key, value in facts.items() if key != 'attributes'} | {'attributes': facts['attributes']}

    @staticmethod
    def _rule_copy(facts):
        model = facts.get('model', '')
        category = facts.get('category', '')
        safe_model = model if model and model.isascii() else ''
        safe_category = category if category and category.isascii() else ''
        title = ' '.join(value for value in (safe_category, safe_model) if value) or 'Product Listing Draft'
        keywords = ['product', safe_model or 'product model', safe_category or 'product specifications']
        keywords = list(dict.fromkeys(keywords))
        while len(keywords) < 3:
            keywords.append(('product specifications', 'product listing')[len(keywords) % 2])
        facts_specs = [
            ('Model', facts.get('model')), ('Power', facts.get('wattage')),
            ('Product size', facts.get('product_dimensions')), ('Carton size', facts.get('carton_dimensions')),
            ('Gross weight', facts.get('gross_weight')),
        ]
        points = []
        for name, value in facts_specs:
            points.append(f'{name}: {value}' if value else f'To be confirmed: {name.lower()}')
        attrs = {key: value or 'To be confirmed: ' + key.lower() for key, value in facts['attributes'].items()}
        details = [f'{key}: {value}' for key, value in facts_specs if value]
        description = ('This draft uses confirmed product records only.\n' + '\n'.join(details)
                       + '\nOther specifications are marked for confirmation.')
        chinese = '给老板核对：\n' + '\n'.join(
            f'{key}：{value if value else "待补充"}' for key, value in (
                ('产品名称', facts.get('name')), ('型号', facts.get('model')), ('功率', facts.get('wattage')),
                ('产品尺寸', facts.get('product_dimensions')), ('外箱尺寸', facts.get('carton_dimensions')),
                ('毛重', facts.get('gross_weight')), ('每箱数量', facts.get('units_per_carton')),
                ('起订量', facts.get('moq')), ('交期', facts.get('lead_time'))))
        return dict(title=title[:128], keywords=keywords[:5], selling_points=points,
                    attributes=attrs, description=description[:2000], chinese_review=chinese[:8000])

    @staticmethod
    def _unsafe_model_text(copy, supplier_terms, offers, facts):
        output = '\n'.join((copy['title'], *copy['keywords'], *copy['selling_points'],
                            *copy['attributes'].values(), copy['description'], copy['chinese_review'])).casefold()
        for term in supplier_terms:
            if len(term.strip()) >= 3 and term.casefold() in output:
                return True
        facts_numbers = set(_NUMBER.findall(dumps(facts)))
        for offer in offers:
            try:
                raw = f"{offer['price_fen'] // 100}.{offer['price_fen'] % 100:02d}"
            except (KeyError, TypeError):
                continue
            if raw not in facts_numbers and re.search(r'(?<!\w)' + re.escape(raw) + r'(?!\w)', output):
                return True
            currency_word = str(offer.get('currency') or '').casefold()
            if currency_word and re.search(r'\b' + re.escape(currency_word) + r'\s*' + re.escape(raw) + r'(?!\w)', output):
                return True
        return False

    def _generate_copy(self, facts, public_snippets, supplier_terms, offers, tone, market, now):
        fallback = self._rule_copy(facts)
        if self.runner is None:
            return fallback, 'rules', None, 'AI 未配置，已使用规则事实生成半成品草稿', None
        try:
            reserve(self.store, now, self.config, bucket='listing')
        except RateLimited:
            return fallback, 'rules', False, '发品员 AI 额度已用尽，已使用规则事实生成半成品草稿', None
        except Exception:
            return fallback, 'rules', False, '发品员 AI 额度服务不可用，已使用规则事实生成半成品草稿', None
        prompt = drafting.make_prompt(self._facts_for_prompt(facts), public_snippets,
                                      tone=tone, target_market=market)
        try:
            parsed = role_ai.invoke(self.runner, prompt, self.config.ai_timeout)
            result = drafting.parse(parsed, facts)
            if self._unsafe_model_text(result, supplier_terms, offers, facts):
                raise ValueError('AI 输出命中内部敏感信息')
            model = safe(parsed.get('model'), 200) if parsed.get('model') else None
            return result, 'ai', True, None, model
        except TimeoutError:
            return fallback, 'rules', False, 'AI 草稿超时，已使用规则事实生成半成品草稿', None
        except Exception:
            return fallback, 'rules', False, 'AI 草稿不可用，已使用规则事实生成半成品草稿', None

    def _image_refs_valid(self, product_id, refs):
        for ref in refs:
            try:
                if not self.media_lookup(product_id, ref):
                    raise BadRequest('图片引用不是已登记且绑定本产品的公开图片')
            except BadRequest:
                raise
            except Exception:
                raise BadRequest('图片引用暂时无法核实') from None

    @staticmethod
    def _external_text(fields):
        attributes = fields.get('attributes') or {}
        packaging = fields.get('packaging') or {}
        return '\n'.join([str(fields.get('title') or ''), str(fields.get('category_hint') or ''),
            *map(str, fields.get('keywords') or []), *map(str, attributes.keys()), *map(str, attributes.values()),
            str(fields.get('unit') or ''), str(fields.get('lead_time_text') or ''),
            *map(str, packaging.keys()), *map(str, packaging.values()),
            *map(str, fields.get('selling_points') or []), str(fields.get('description') or '')]).casefold()

    def _assert_public_safe(self, db, product_id, fields):
        external = self._external_text(fields)
        terms = []
        for row in db.execute("SELECT name,aliases_json FROM parties WHERE kind IN ('supplier','customer')"):
            terms.append(row['name'])
            terms.extend(_j(row['aliases_json'] or '[]', []))
        if any(isinstance(term, str) and len(term.strip()) >= 3 and term.casefold() in external for term in terms):
            raise Conflict('对外内容含档案中的供应商或客户名称，请清除后再审核或导出')
        symbols = {'CNY': r'[¥￥]', 'RMB': r'[¥￥]', 'USD': r'\$', 'EUR': '€', 'GBP': '£'}
        for offer in db.execute('SELECT price_fen,currency FROM offers WHERE product_id=?', (product_id,)):
            amount = f"{offer['price_fen'] // 100}.{offer['price_fen'] % 100:02d}"
            code = str(offer['currency'] or '').upper()
            if code and re.search(r'(?<![A-Z])' + re.escape(code) + r'\s*' + re.escape(amount) + r'(?!\d)', external, re.I):
                raise Conflict('对外内容疑似含供应商成本金额，请核对后再审核或导出')
            symbol = symbols.get(code)
            if symbol and re.search(symbol + r'\s*' + re.escape(amount) + r'(?!\d)', external):
                raise Conflict('对外内容疑似含供应商成本金额，请核对后再审核或导出')
            if re.search(r'(?:cost|purchase\s+price|supplier\s+price|成本|采购价).{0,24}'
                         + re.escape(amount), external, re.I):
                raise Conflict('对外内容疑似含供应商成本金额，请核对后再审核或导出')

    @serialized
    def generate(self, raw, who):
        params = validation.generate(raw)
        now = self.clock()
        today = self.day()
        product, offers, product_sources, freight_quotes, supplier_terms = self._product_snapshot(
            params['product_id'], params.get('target_market', ''))
        facts, current = self._fact_snapshot(product, offers, today)
        facts = self._remove_supplier_terms(facts, supplier_terms)
        public_snippets = self._knowledge(facts, supplier_terms)
        try:
            fx_data = self.fx_reader() if self.fx_reader else {}
            fx_data = dict(fx_data) if isinstance(fx_data, dict) else {}
            fx_data = {key: str(value) if key in ('usd_cny', 'source') and value is not None else value
                       for key, value in fx_data.items() if key in ('usd_cny', 'source', 'as_of')}
        except Exception:
            fx_data = {}
        tiers, internal_pricing, price_sources = pricing.suggest(
            product, offers, freight_quotes, today, fx_data=fx_data,
            target_margin_bp=params.get('target_margin_bp', self.config.default_target_margin_bp),
            target_market=params.get('target_market', ''))
        copy, generation_source, ai_success, ai_error, model = self._generate_copy(
            facts, public_snippets, supplier_terms, offers, params.get('tone', ''),
            params.get('target_market', ''), now)
        packaging = {}
        if facts['carton_dimensions']:
            packaging['carton_dimensions_cm'] = facts['carton_dimensions']
        if facts['gross_weight']:
            packaging['gross_weight'] = facts['gross_weight']
        if facts['units_per_carton']:
            packaging['units_per_carton'] = int(facts['units_per_carton'])
        fields = dict(
            title=copy['title'], keywords=copy['keywords'], category_hint=facts.get('category') or 'To be confirmed',
            attributes=copy['attributes'], moq=int(facts['moq']) if facts.get('moq') else None,
            unit=facts.get('unit') or '', price_tiers=tiers, lead_time_text=facts.get('lead_time') or '',
            packaging=packaging, selling_points=copy['selling_points'], description=copy['description'],
            image_refs=[], chinese_review=copy['chinese_review'])
        fields['compliance'] = compliance.check(fields, brand_terms=self.brand_terms,
            image_count=0, image_dimensions_known=False)
        evidence = ([dict(kind='product_source', id=row['id'], type=row['kind']) for row in product_sources]
                    + price_sources + [dict(kind='kb_chunk', id=row['source_id']) for row in public_snippets])
        inputs = dict(version=1, product_id=product['id'], channel=params['channel'], facts=facts,
                      references=[*evidence], target_margin_bp=params.get('target_margin_bp',
                      self.config.default_target_margin_bp), fx=fx_data, market=params.get('target_market', ''),
                      tone=params.get('tone', ''), source_offer={k: current.get(k) for k in
                      ('id', 'price_fen', 'currency', 'moq_units', 'unit', 'valid_until', 'updated_at')}
                      if current else None)
        input_fp = fingerprint(inputs)
        with self.store.write() as db:
            self._assert_public_safe(db, product['id'], fields)
            stamp_now = stamp(now)
            draft_id = insert(db, 'listing_drafts', dict(
                product_id=product['id'], channel=params['channel'], status='draft', title=fields['title'],
                keywords_json=dumps(fields['keywords']), category_hint=fields['category_hint'],
                attributes_json=dumps(fields['attributes']), moq=fields['moq'], unit=fields['unit'],
                price_tiers_json=dumps(fields['price_tiers']), lead_time_text=fields['lead_time_text'],
                packaging_json=dumps(fields['packaging']), selling_points_json=dumps(fields['selling_points']),
                description=fields['description'], image_refs_json='[]', chinese_review=fields['chinese_review'],
                compliance_json=dumps(fields['compliance']), sources_json=dumps(evidence),
                generation_source=generation_source, internal_pricing_json=dumps(internal_pricing),
                input_fingerprint=input_fp, created_at=stamp_now, updated_at=stamp_now,
                created_by=who, decided_at=None, decided_by=None, published_ref=None), who)
            subject_key = f"listing:{product['id']}:{params['channel']}:{draft_id}"
            run_status = 'ok' if generation_source == 'ai' else 'fallback'
            save_run(db, 'listing', 'product', subject_key, input_fp,
                     dict(draft_id=draft_id, generation_source=generation_source, ai_success=ai_success,
                          title=fields['title'], compliance=fields['compliance']),
                     fields['title'] or 'Listing draft generated from confirmed product facts', evidence, who, now,
                     source=generation_source, status=run_status,
                     model=model, error=ai_error, success=ai_success)
        result = self._detail(draft_id)
        result['ai_message'] = ai_error
        return result

    def _row(self, db, draft_id):
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_drafts'").fetchone()
        if not exists:
            raise Missing('未找到该发品草稿')
        row = db.execute('SELECT * FROM listing_drafts WHERE id=?', (draft_id,)).fetchone()
        if row is None:
            raise Missing('未找到该发品草稿')
        return dict(row)

    def _detail(self, draft_id):
        with self.store.read() as db:
            row = self._row(db, draft_id)
            result = _listing_values(row)
            key = f"listing:{row['product_id']}:{row['channel']}:{row['id']}"
            run = db.execute("SELECT * FROM role_runs WHERE role='listing' AND subject_key=? ORDER BY id DESC LIMIT 1",
                             (key,)).fetchone() if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='role_runs'").fetchone() else None
            result['run'] = dict(run) if run else None
            if result['run']:
                result['run']['output_json'] = _j(result['run'].get('output_json'), {})
                result['run']['evidence_json'] = _j(result['run'].get('evidence_json'), [])
                result['run']['ai_success'] = bool(result['run']['ai_success']) if result['run']['ai_success'] is not None else None
            return result

    def _list_drafts(self, params):
        filters = validation.query(params)
        with self.store.read() as db:
            available = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_drafts'").fetchone()
            if not available:
                return dict(items=[], total=0, page=filters['page'], page_size=filters['page_size'])
            where, args = [], []
            for key, column in (('product_id', 'product_id'), ('channel', 'channel'), ('status', 'status')):
                if filters.get(key) is not None:
                    where.append(column + '=?')
                    args.append(filters[key])
            clause = ' WHERE ' + ' AND '.join(where) if where else ''
            total = db.execute('SELECT count(*) FROM listing_drafts' + clause, args).fetchone()[0]
            start = (filters['page'] - 1) * filters['page_size']
            rows = [dict(row) for row in db.execute('SELECT * FROM listing_drafts' + clause +
                ' ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?', (*args, filters['page_size'], start))]
        return dict(items=[_listing_values(row) for row in rows], total=total,
                    page=filters['page'], page_size=filters['page_size'])

    def _products_ready(self, params):
        filters = validation.query(params, 'products_ready')
        today = self.day()
        with self.store.read() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'products' not in tables:
                return dict(items=[], total=0, page=filters['page'], page_size=filters['page_size'])
            products = [dict(row) for row in db.execute("SELECT p.* FROM products p WHERE p.status!='hidden' "
                "AND NOT EXISTS (SELECT 1 FROM product_merges m WHERE m.product_id=p.id) ORDER BY p.id")]
            offers = defaultdict(list)
            if 'offers' in tables:
                for row in db.execute("SELECT * FROM offers WHERE status='confirmed' ORDER BY product_id,id"):
                    offers[row['product_id']].append(dict(row))
        items = []
        for product in products:
            missing = []
            if product['status'] != 'confirmed':
                missing.append('先确认产品档案')
            if not product.get('name'):
                missing.append('产品名称')
            if not product.get('model'):
                missing.append('型号')
            if product.get('wattage') is None:
                missing.append('功率')
            if not all(product.get(key) is not None for key in
                       ('carton_l_cm', 'carton_w_cm', 'carton_h_cm')):
                missing.append('外箱尺寸')
            if product.get('gross_kg') is None:
                missing.append('毛重')
            if product.get('units_per_carton') is None:
                missing.append('每箱数量')
            offer = pricing.current_usable_offer(offers.get(product['id'], []), today)
            if not offer:
                missing.append('有效成本')
            elif offer.get('moq_units') is None:
                missing.append('成本报价起订量')
            if offer and not offer.get('unit'):
                missing.append('成本报价计价单位')
            items.append(dict(product_id=product['id'], name=safe(product.get('name'), 200),
                              model=safe(product.get('model'), 200) if product.get('model') else '',
                              status=product['status'], ready=not missing, missing=missing,
                              valid_cost_offer_id=offer['id'] if offer else None))
        total = len(items)
        start = (filters['page'] - 1) * filters['page_size']
        return dict(items=items[start:start + filters['page_size']], total=total,
                    page=filters['page'], page_size=filters['page_size'])

    def read(self, resource, params=None, row_id=None, export_format=None):
        params = params or {}
        if resource == 'drafts':
            return self._list_drafts(params)
        if resource == 'draft_detail':
            return self._detail(row_id)
        if resource == 'products_ready':
            return self._products_ready(params)
        if resource == 'export':
            with self.store.read() as db:
                raw = self._row(db, row_id)
                result = _listing_values(raw)
                self._assert_public_safe(db, raw['product_id'], result)
            return export(result, export_format or 'text')
        raise BadRequest('发品资源无效')

    @serialized
    def write(self, operation, row_id, raw, who):
        if operation == 'generate':
            return self.generate(raw, who)
        if row_id is None:
            raise BadRequest('缺少草稿编号')
        if operation == 'update':
            values = validation.edit(raw)
            return self._update(row_id, values, who)
        if operation == 'status':
            values = validation.status(raw)
            return self._status(row_id, values, who)
        raise BadRequest('发品操作无效')

    def _update(self, draft_id, values, who):
        with self.store.write() as db:
            old = self._row(db, draft_id)
            if old['status'] in ('published', 'archived'):
                raise Conflict('已发布或已归档草稿不可修改')
            current = _listing_values(old)
            merged = dict(current)
            for field, value in values.items():
                target = field.removesuffix('_json') if field.endswith('_json') else field
                merged[target] = _j(value, {}) if target in ('attributes', 'packaging') else _j(value, []) if target in (
                    'keywords', 'price_tiers', 'selling_points', 'image_refs') else value
            self._assert_public_safe(db, old['product_id'], merged)
            self._image_refs_valid(old['product_id'], merged.get('image_refs', []))
            merged['compliance'] = compliance.check(merged, brand_terms=self.brand_terms,
                image_count=len(merged.get('image_refs') or []), image_dimensions_known=False)
            self._update_values(db, old, values, merged, who, stamp(self.clock()))
        return self._detail(draft_id)

    @staticmethod
    def _update_values(db, old, values, merged, who, updated_at):
        row_values = dict(values)
        row_values['compliance_json'] = dumps(merged['compliance'])
        row_values['status'] = 'draft'
        row_values['updated_at'] = updated_at
        row_values['decided_at'] = None
        row_values['decided_by'] = None
        row_values['published_ref'] = None
        update(db, 'listing_drafts', old['id'], row_values, who)

    def _status(self, draft_id, values, who):
        now = stamp(self.clock())
        with self.store.write() as db:
            old = self._row(db, draft_id)
            new_status = values['status']
            if old['status'] == 'published' and new_status != 'archived':
                raise Conflict('已发布草稿不能改回待审核状态')
            if new_status == 'ready':
                if old['status'] not in ('draft', 'ready'):
                    raise Conflict('只有草稿可以标记可以发')
                report = _j(old['compliance_json'], {})
                if report.get('blocking'):
                    raise Conflict('尚有阻断级合规问题，无法标记可以发')
                self._assert_public_safe(db, old['product_id'], _listing_values(old))
                decided_at, decided_by, published_ref = now, who, None
            elif new_status == 'published':
                if old['status'] != 'ready':
                    raise Conflict('请先审核并标记可以发，再记录已发布')
                self._assert_public_safe(db, old['product_id'], _listing_values(old))
                decided_at, decided_by, published_ref = now, who, values['published_ref']
            elif new_status == 'archived':
                decided_at, decided_by, published_ref = now, who, old.get('published_ref')
            else:
                if old['status'] == 'published':
                    raise Conflict('已发布记录不能改回草稿')
                decided_at, decided_by, published_ref = None, None, None
            update(db, 'listing_drafts', draft_id, dict(status=new_status, updated_at=now,
                   decided_at=decided_at, decided_by=decided_by, published_ref=published_ref), who,
                   'status_' + new_status)
        return self._detail(draft_id)


def export(draft, format_name='text'):
    """Export only customer-facing fields in listing-page order."""
    fields = [
        ('Category', draft.get('category_hint') or ''),
        ('Title', draft.get('title') or ''),
        ('Keywords', '; '.join(draft.get('keywords') or [])),
        ('Attributes', '\n'.join(f'{key}: {value}' for key, value in (draft.get('attributes') or {}).items())),
        ('Minimum order quantity', draft.get('moq') if draft.get('moq') is not None else ''),
        ('Unit', draft.get('unit') or ''),
        ('Price tiers', '\n'.join(f"{tier['min_qty']}+ {tier['price']} {tier['currency']}"
                                   for tier in draft.get('price_tiers') or [])),
        ('Lead time', draft.get('lead_time_text') or ''),
        ('Packaging', dumps(draft.get('packaging') or {})),
        ('Selling points', '\n'.join(draft.get('selling_points') or [])),
        ('Description', draft.get('description') or ''),
        ('Product images', '\n'.join(draft.get('image_refs') or [])),
    ]
    if format_name == 'csv':
        output = StringIO(newline='')
        writer = csv.writer(output, lineterminator='\r\n')
        writer.writerow(('Field', 'Value'))
        writer.writerows(fields)
        return dict(format='csv', content=output.getvalue())
    if format_name != 'text':
        raise BadRequest('导出格式须为 text 或 csv')
    return dict(format='text', content='\n\n'.join(f'{key}\n{value}' for key, value in fields))

