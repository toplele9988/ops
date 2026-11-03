import json

import pytest

from console.archive.store import Store
from console.archive.validation import BadRequest, Conflict, Missing
from console.listing import compliance
from console.listing.service import export
from console.roles.service import Roles

def test_generate_uses_confirmed_facts_public_kb_and_separate_internal_price(env):
    draft = env.generate(target_margin='20', target_market='Fictional Country')
    assert draft['generation_source'] == 'ai' and draft['source'] == 'ai'
    assert draft['run']['role'] == 'listing' and draft['run']['model']
    assert draft['status'] == 'draft' and draft['title'] == '120 W LED Flood Light X900'
    assert draft['keywords'] == ['LED flood light', 'stage lighting', 'X900']
    assert draft['moq'] == 2 and draft['unit'] == 'pcs'
    assert draft['price_tiers'] == [dict(min_qty=2, price='18.08', currency='USD')]
    assert draft['internal_pricing']['cost_cny_total'] == '202.46'
    assert draft['compliance']['blocking'] == []
    assert any(i['code'] == 'missing_attributes' for i in draft['compliance']['warnings'])
    assert '待补充' in draft['chinese_review']
    assert 'To be confirmed: voltage' in draft['attributes']['Voltage']
    assert any(source['id'] == 'kb_public_fixture_001' for source in draft['sources'])
    assert not any(source['id'] in ('kb_supplier_secret', 'kb_internal_technical', 'kb_public_cost_labeled')
                   for source in draft['sources'])
    prompt = env.calls[-1]
    for secret in ('FICTIONAL_FACTORY_SECRET', 'Fictional Buyer Alice', '999.99', '101.23', '202.46'):
        assert secret not in prompt
    assert 'The fictional X900 is recorded as a 120 W fixture.' in prompt
    text = export(draft, 'text')['content']
    csv_text = export(draft, 'csv')['content']
    for secret in ('FICTIONAL_FACTORY_SECRET', 'Fictional Buyer Alice', '101.23', '202.46', 'kb_public_fixture_001'):
        assert secret not in text and secret not in csv_text
    assert '18.08 USD' in text and '18.08' in csv_text
    assert text.index('Category') < text.index('Title') < text.index('Keywords') < text.index('Attributes')


def test_rules_fallback_and_missing_cost_do_not_invent_a_price(env):
    env.runner = env.failing_runner
    service = env.make_service(runner=env.failing_runner)
    product = env.archive.write('product_create', None, dict(name='Fictional Unpriced Lamp', model='NO-COST-4'),
                                env.owner)['id']
    env.archive.write('product_confirm', product, {}, env.owner)
    draft = service.generate(dict(product_id=str(product), channel='alibaba'), env.owner)
    assert draft['generation_source'] == 'rules'
    assert draft['status'] == 'draft' and draft['title'] == 'NO-COST-4'
    assert len(draft['keywords']) == 3 and len(draft['selling_points']) == 5
    assert draft['price_tiers'] == []
    assert draft['internal_pricing']['available'] is False
    assert '已确认成本报价' in draft['internal_pricing']['reason']
    assert draft['run']['source'] == 'rules' and draft['run']['status'] == 'fallback'
    assert draft['run']['ai_success'] is False


def test_pricing_uses_quote_kernel_and_one_unique_valid_freight(env):
    env.archive.write('freight_create', None, dict(
        forwarder_id=str(env.archive.write('party_create', None,
            dict(kind='forwarder', name='FICTIONAL_FORWARDER'), env.owner)['id']),
        origin='Fictional Origin', destination_country='Fictional Country', mode='sea', service='Fictional Service',
        unit='kg', price='2.00', currency='CNY', quoted_on='2030-01-01', valid_until='2030-02-20',
        transit_days_min='10', transit_days_max='20'), env.owner)
    with_freight = env.generate(target_market='Fictional Country', target_margin='20')
    assert with_freight['price_tiers'][0]['price'] == '18.97'
    assert with_freight['internal_pricing']['freight_quote_id'] is not None
    assert any(source['kind'] == 'freight_quote' for source in with_freight['sources'])

    env.archive.write('freight_create', None, dict(
        forwarder_id=str(env.archive.write('party_create', None,
            dict(kind='forwarder', name='FICTIONAL_SECOND_FORWARDER'), env.owner)['id']),
        origin='Fictional Origin', destination_country='Fictional Country', mode='air', service='Fictional Air',
        unit='kg', price='1.00', currency='CNY', quoted_on='2030-01-01', valid_until='2030-02-20'), env.owner)
    ambiguous = env.generate(target_market='Fictional Country', target_margin='20')
    assert ambiguous['internal_pricing']['freight_quote_id'] is None
    assert '唯一' in ambiguous['internal_pricing']['freight_note']


def test_ready_published_update_and_archive_transitions(env):
    draft = env.generate()
    env.fw('update', draft['id'], title='Best LED Flood Light X900')
    with pytest.raises(Conflict, match='阻断级'):
        env.fw('status', draft['id'], status='ready')
    blocked = env.service.read('draft_detail', row_id=draft['id'])
    assert blocked['status'] == 'draft'
    assert any(issue['code'] == 'prohibited_claim' for issue in blocked['compliance']['blocking'])
    with pytest.raises(BadRequest, match='确认'):
        env.fw('status', draft['id'], status='published')

    env.fw('update', draft['id'], title='120 W LED Flood Light X900')
    ready = env.fw('status', draft['id'], status='ready')
    assert ready['status'] == 'ready' and ready['decided_by'] == env.owner and ready['decided_at']
    with pytest.raises(BadRequest, match='链接或 ID'):
        env.fw('status', draft['id'], status='published', confirm='1')
    published = env.fw('status', draft['id'], status='published', confirm='1',
                       published_ref='https://alibaba.example/item/fixture-1')
    assert published['status'] == 'published' and published['published_ref'].endswith('fixture-1')
    with pytest.raises(Conflict, match='不可修改'):
        env.fw('update', draft['id'], title='Edited after publish')
    archived = env.fw('status', draft['id'], status='archived')
    assert archived['status'] == 'archived' and archived['published_ref'] == published['published_ref']


def test_manual_update_rechecks_compliance_and_large_description_limit(env):
    draft = env.generate()
    long_description = 'x' * 65536
    assert len(long_description) <= 65536
    updated = env.fw('update', draft['id'], description=long_description)
    assert updated['description'] == long_description
    assert updated['compliance']['ready'] is True
    with pytest.raises(BadRequest):
        env.fw('update', draft['id'], description='字' * 65537)
    with pytest.raises(BadRequest, match='不支持修改'):
        env.fw('update', draft['id'], supplier_name='FICTIONAL_FACTORY_SECRET')


def test_manual_update_blocks_known_supplier_customer_and_cost_leaks(env):
    env.archive.write('party_create', None, dict(kind='customer', name='FICTIONAL_BUYER_SECRET'), env.owner)
    draft = env.generate()
    for text in ('FICTIONAL_FACTORY_SECRET', 'FICTIONAL_BUYER_SECRET', 'CNY 101.23', 'supplier cost 101.23'):
        with pytest.raises(Conflict):
            env.fw('update', draft['id'], description=text)


def test_products_ready_explains_missing_facts_and_valid_cost(env):
    data = env.service.read('products_ready', {})
    ready = next(item for item in data['items'] if item['product_id'] == env.product)
    assert ready['ready'] is True and ready['missing'] == []
    second = env.archive.write('product_create', None, dict(name='Fictional Incomplete Lamp', model='MISSING-4'), env.owner)['id']
    env.archive.write('product_confirm', second, {}, env.owner)
    data = env.service.read('products_ready', {})
    missing = next(item for item in data['items'] if item['product_id'] == second)
    assert missing['ready'] is False
    assert {'功率', '外箱尺寸', '毛重', '每箱数量', '有效成本'} <= set(missing['missing'])


def test_compliance_brand_hype_and_information_prompts_are_configurable():
    draft = dict(title='x' * 129 + ' Guaranteed best BLOCKED BRAND fixture', keywords=['one'], selling_points=[],
                 attributes={'Voltage': 'To be confirmed: voltage'}, description='')
    report = compliance.check(draft, brand_terms=('BLOCKED BRAND',))
    codes = {issue['code'] for issue in report['blocking']}
    assert {'title_length', 'keyword_count', 'prohibited_claim', 'brand_term'} <= codes
    assert any(issue['code'] == 'missing_attributes' and issue['severity'] == 'warning'
               for issue in report['warnings'])
    assert any(issue['code'] == 'image_count' and issue['severity'] == 'info' for issue in report['issues'])


def test_independent_quota_and_role_runs_registration(env):
    from console.roles.storage import reserve

    # Each role run reserves the official runner's three possible attempts;
    # the shared role lane remains independent.
    last = None
    for _ in range(13):
        last = env.generate()
    assert last['generation_source'] == 'rules' and len(env.calls) == 12
    with env.store.read() as db:
        listing_calls = db.execute("SELECT sum(calls) FROM role_ai_reservations WHERE bucket='listing'").fetchone()[0]
        shared_calls = db.execute("SELECT count(*) FROM role_ai_reservations WHERE bucket='shared'").fetchone()[0]
        assert listing_calls == 36 and shared_calls == 0
    reserve(env.service.store, env.clock(), env.config, bucket='shared')
    with env.store.read() as db:
        assert db.execute("SELECT sum(calls) FROM role_ai_reservations WHERE bucket='listing'").fetchone()[0] == 36
        assert db.execute("SELECT sum(calls) FROM role_ai_reservations WHERE bucket='shared'").fetchone()[0] == 3
    roles = Roles(env.path, runner=None, inbox=object(), clock=env.clock)
    listing_role = next(item for item in roles.read('roles')['items'] if item['key'] == 'listing')
    assert listing_role['name'] == '发品员' and listing_role['level'] == 'L1'
    assert listing_role['runs_24h'] == 13
    assert len(roles.read('runs', {'role': 'listing', 'limit': 20})['items']) == 13


def test_unknown_ids_and_sources_are_ids_without_copied_body(env):
    with pytest.raises(Missing):
        env.service.read('draft_detail', row_id=999999)
    draft = env.generate()
    serialized_sources = json.dumps(draft['sources'], ensure_ascii=False)
    assert 'Fictional internal title' not in serialized_sources
    assert 'Fictional Buyer Alice' not in serialized_sources
    assert any(source['kind'] == 'product_source' for source in draft['sources'])


def test_manual_changes_write_value_free_archive_audit(env):
    draft = env.generate()
    env.fw('update', draft['id'], title='Revised fictional X900 listing')
    with env.store.read() as db:
        rows = list(db.execute("SELECT detail FROM audit WHERE table_name='listing_drafts' AND row_id=? "
                               "ORDER BY id", (draft['id'],)))
    assert len(rows) >= 2
    latest = json.loads(rows[-1]['detail'])
    assert 'title' in latest['fields'] and 'compliance_json' in latest['fields']
    assert 'Revised fictional X900 listing' not in rows[-1]['detail']


def test_v10_migration_preserves_role_history_and_shared_quota_rows(tmp_path):
    from console.roles.storage import save_run

    path = tmp_path / 'fictional-v9.sqlite3'
    old = Store(path, target_version=9)
    with old.write() as db:
        save_run(db, 'audit', 'day', 'fictional-day', 'fictional-fingerprint', {'ok': True},
                 'Fictional prior run', [], 'fictional-owner', 1894262400)
        db.execute('INSERT INTO role_ai_reservations(at,calls) VALUES (?,?)', (1894262400, 3))
    Store(path, target_version=10).initialize()
    with Store(path, target_version=10).read() as db:
        assert [row[0] for row in db.execute('SELECT version FROM schema_migrations ORDER BY version')][-1] == 10
        assert db.execute("SELECT count(*) FROM role_runs WHERE role='audit' AND subject_key='fictional-day'").fetchone()[0] == 1
        assert db.execute("SELECT bucket FROM role_ai_reservations").fetchone()[0] == 'shared'
    with Store(path, target_version=10).write() as db:
        listing = dict(role='listing', subject_kind='product', subject_key='fictional:1',
                       input_fingerprint='fictional-listing-fp', status='fallback', source='rules', model=None,
                       output_json='{}', output_text='Fictional listing run', evidence_json='[]', error=None,
                       created_at='2030-01-10T00:00:00+00:00', created_by='fictional-owner', ai_success=0)
        db.execute('INSERT INTO role_runs(' + ','.join(listing) + ') VALUES (' + ','.join('?' for _ in listing) + ')',
                   tuple(listing.values()))
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []

