from datetime import date
import json
from unittest.mock import patch

import pytest

from console.archive import validation as v
from console.archive.store import Store
from console.deals.service import Deals
from console.fulfil.service import Fulfil
from console.fulfil.nodes import NODES


def test_all_nodes_record_events_and_only_suggest_stages(fulfil_env):
    e = fulfil_env
    before = e.deals.read('detail', row_id=e.did)
    for node in NODES:
        fields = {'status': 'done', 'note': '虚构处理记录'}
        if node == 'supplier_confirmation':
            fields['promised_on'] = '2030-01-06'
        if node == 'inspection':
            fields.update(inspection_result='pass', inspector='虚构验货公司')
        if node == 'booking':
            fields.update(carrier='虚构船司', tracking_number='FAKE-1234', etd='2030-01-07', eta='2030-02-01')
        e.write('node_update', child=node, **fields)
    view = e.service.read('fulfilment', row_id=e.did)
    assert view['done'] == view['total'] == 8
    assert {s['stage'] for s in view['suggestions']} == {'inspection', 'shipped', 'delivered'}
    after = e.deals.read('detail', row_id=e.did)
    assert (after['stage'], after['status'], after['won_at']) == (before['stage'], before['status'], before['won_at'])
    assert len([ev for ev in after['events'] if ev['summary'].startswith('履约：')]) == 8
    assert next(n for n in view['items'] if n['node'] == 'signoff')['signed_on'] == '2030-01-01'


def test_node_id_idempotency_reopening_and_explicit_fail(fulfil_env):
    e = fulfil_env
    row_id = e.write('node_update', child='inspection', status='done', inspection_result='fail')['id']
    snapshot = e.deals.read('detail', row_id=e.did)
    e.write('node_update_id', row_id, status='done', inspection_result='fail')
    assert snapshot == e.deals.read('detail', row_id=e.did)
    assert e.service.read('fulfilment', row_id=e.did)['suggestions'] == []
    e.write('node_update_id', row_id, status='doing')
    inspection = next(n for n in e.service.read('fulfilment', row_id=e.did)['items'] if n['node'] == 'inspection')
    assert inspection['completed_on'] is None
    e.write('node_update', child='signoff', status='done')
    e.write('node_update', child='signoff', status='doing')
    assert next(n for n in e.service.read('fulfilment', row_id=e.did)['items'] if n['node'] == 'signoff')['signed_on'] is None


@pytest.mark.parametrize('node,fields', [
    ('inspection', {'status': 'done'}), ('supplier_confirmation', {'status': 'done'}),
    ('po', {'eta': '2030-01-02'}), ('po', {'planned_on': '2030-02-30'}),
    ('po', {'status': 'made-up'}), ('po', {'responsible': 'customer'}),
    ('signoff', {'signed_on': '2030-01-01'}), ('po', {}),
    ('po', {'status': 'done', 'completed_on': '2030-01-02'}),
    ('booking', {'departed_on': '2030-01-02'}),
    ('signoff', {'status': 'done', 'signed_on': '2030-01-02'}),
])
def test_node_validation(fulfil_env, node, fields):
    with pytest.raises(v.BadRequest):
        fulfil_env.write('node_update', child=node, **fields)


def test_node_requires_paid_and_matching_party(fulfil_env):
    e = fulfil_env
    before_payment = e.deal('inquiry')
    with pytest.raises(v.BadRequest, match='收款'):
        e.write('node_update', before_payment, 'po', status='doing')
    with pytest.raises(v.BadRequest, match='类型'):
        e.write('node_update', child='booking', party_id=str(e.supplier))
    e.write('node_update', child='booking', party_id=str(e.forwarder), status='doing')


def test_board_keeps_done_promise_and_eta_actionable_and_sorts(fulfil_env):
    e = fulfil_env
    e.write('node_update', child='supplier_confirmation', status='done', promised_on='2029-12-30')
    near = e.deal()
    e.write('node_update', near, 'booking', status='done', eta='2030-01-03')
    none = e.deal()
    board = e.service.read('board')['items']
    assert [d['deal_id'] for d in board] == [e.did, near, none]
    assert board[0]['overdue'] and not board[1]['overdue']
    e.write('node_update', child='transit', status='doing')
    assert e.service.read('fulfilment', row_id=e.did)['next_due'] is None


def test_issue_lifecycle_append_history_and_impact_without_ledger_mutation(fulfil_env):
    e = fulfil_env
    _, line = e.quote()
    iid = e.write('issue_create', kind='quality', description='虚构灯罩划痕', quote_line_id=str(line),
                  responsible='supplier', party_id=str(e.supplier), severity='high')['id']
    e.write('issue_note', iid, note='虚构首次核实')
    e.write('issue_update', iid, status='waiting_party')
    with pytest.raises(v.BadRequest):
        e.write('issue_update', iid, status='closed', resolution='虚构已处理')
    with pytest.raises(v.BadRequest):
        e.write('issue_update', iid, status='resolved')
    e.write('issue_update', iid, status='resolved', resolution='虚构补件解决',
            impact_kind='compensation', impact_fen='700', currency='USD')
    first = e.service.read('issues', row_id=e.did)['items'][0]
    e.write('issue_update', iid, status='closed')
    closed = e.service.read('issues', row_id=e.did)['items'][0]
    assert closed['status'] == 'closed' and closed['resolved_at'] and closed['closed_at']
    assert closed['product_id'] == e.product and closed['impact_fen'] == 700
    assert closed['notes'][:len(first['notes'])] == first['notes']
    assert all(n['who'] == 'fictional-owner' and n['at'] for n in closed['notes'])
    assert e.service.read('payments', row_id=e.did)['items'] == []
    assert e.deals.read('detail', row_id=e.did)['stage'] == 'paid'
    e.write('issue_update', iid, status='open')
    assert e.service.read('issues', row_id=e.did)['items'][0]['resolved_at'] is None


@pytest.mark.parametrize('fields', [
    {'impact_fen': '1.00'}, {'impact_fen': '-1'}, {'impact_fen': '1e3'}, {'impact_fen': '100'},
    {'severity': 'urgent'}, {'status': 'automatic'}, {'description': ''},
])
def test_issue_validation(fulfil_env, fields):
    e = fulfil_env
    iid = e.write('issue_create', kind='feedback', description='虚构意见')['id']
    with pytest.raises(v.BadRequest):
        e.write('issue_update', iid, **fields)


def test_issue_cannot_cross_deals_or_responsible_party(fulfil_env):
    e = fulfil_env
    other = e.deal()
    _, foreign_line = e.quote(other)
    with pytest.raises(v.BadRequest, match='不属于'):
        e.write('issue_create', kind='quality', description='虚构问题', quote_line_id=str(foreign_line))
    with pytest.raises(v.BadRequest, match='类型'):
        e.write('issue_create', kind='quality', description='虚构问题', responsible='forwarder', party_id=str(e.supplier))


def test_issue_summary_date_filters_and_unresolved_grouping(fulfil_env):
    e = fulfil_env
    one = e.write('issue_create', kind='quality', description='虚构问题', product_id=str(e.product),
                  responsible='supplier', party_id=str(e.supplier))['id']
    e.write('issue_create', kind='delay', description='虚构延误', responsible='forwarder', party_id=str(e.forwarder))
    e.write('issue_update', one, status='waiting_party')
    summary = e.service.read('issue_summary', {'start': '2030-01-01', 'end': '2030-01-01'})
    assert summary['total'] == summary['unresolved'] == 2
    assert summary['products'][0] == dict(product_id=e.product, count=1, unresolved=1)
    assert {p['responsible'] for p in summary['parties']} == {'supplier', 'forwarder'}
    e.write('issue_update', one, status='resolved', resolution='虚构已补件')
    assert e.service.read('issue_summary')['unresolved'] == 1
    assert e.service.read('issue_summary', {'end': '2029-12-31'})['total'] == 0
    with pytest.raises(v.BadRequest):
        e.service.read('issue_summary', {'start': '2030-01-02', 'end': '2030-01-01'})


def test_issue_draft_excludes_internal_fields_and_suspect_prose(fulfil_env):
    e = fulfil_env
    _, line = e.quote()
    other = e.deal(title='另一虚构客户PRIVATE-BUYER')
    e.quote(other)
    iid = e.write('issue_create', kind='quality', description='虚构灯罩划痕；成本秘密1379；利润4321；其他客户PRIVATE-BUYER',
                  quote_line_id=str(line), responsible='supplier', party_id=str(e.supplier))['id']
    e.write('issue_note', iid, note='内部处理预算FICTIONAL-SECRET')
    result = e.service.read('issue_draft', {'target': 'forwarder'}, iid)
    assert '质量' in result['text'] and result['copy_only'] and result['source'] == 'rules'
    assert result['needs_manual_details'] and '虚构灯罩划痕' not in result['text']
    for forbidden in ('1379', '4321', 'PRIVATE-BUYER', 'FICTIONAL-SECRET', '成本', '利润'):
        assert forbidden not in result['text']


def test_issue_summary_and_old_delivered_reorder_use_business_day(fulfil_env):
    e = fulfil_env
    # 16:30 UTC is next morning in the common Shanghai business timezone.
    e.service.clock = lambda: 1893515400  # 2030-01-01 16:30 UTC
    e.write('issue_create', kind='feedback', description='虚构跨日意见')
    assert e.service.read('issue_summary', {'end': '2030-01-01'})['total'] == 0
    assert e.service.read('issue_summary', {'start': '2030-01-02', 'end': '2030-01-02'})['total'] == 1
    e.deals.clock = e.service.clock
    delivered = e.deal('delivered')
    e.service.today = lambda: date(2030, 2, 1)
    lead = e.service.read('reorder_leads')['items'][0]
    assert lead['deal_id'] == delivered and lead['signed_on'] == '2030-01-02' and lead['days_since_signed'] == 30


def test_reorder_thresholds_sale_price_and_new_inquiry_suppression(fulfil_env):
    e = fulfil_env
    e.quote()
    e.write('node_update', child='signoff', status='done', signed_on='2029-11-02')
    lead = e.service.read('reorder_leads')['items'][0]
    assert lead['days_since_signed'] == 60 and lead['reached_days'] == [30, 60]
    assert lead['products'][0]['sell_unit_minor'] == 3500
    assert not any(word in json.dumps(lead) for word in ('cost', '1379', 'target_price', 'note'))
    assert e.service.read('reorder_leads', {'days': (61,)})['items'] == []
    assert e.service.read('reorder_leads', {'days': '60'})['items']
    e.deal('inquiry')
    assert e.service.read('reorder_leads')['items'] == []


def test_reorder_copy_uses_deal_create_and_copies_no_money_quotes_or_notes(fulfil_env):
    e = fulfil_env
    e.quote()
    e.write('node_update', child='signoff', status='done')
    result = e.write('reorder')
    detail = e.deals.read('detail', row_id=result['id'])
    assert detail['stage'] == 'inquiry' and detail['status'] == 'open' and detail['won_at'] is None
    assert detail['customer']['id'] == e.customer and '返单' in detail['title']
    assert detail['value'] is None
    assert detail['items'][0]['product_id'] == e.product and detail['items'][0]['qty'] == 2
    assert detail['items'][0]['target_price'] is None and detail['items'][0]['note'] is None
    with e.store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM quote_rounds WHERE deal_id=?', (result['id'],)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM deal_payments WHERE deal_id=?', (result['id'],)).fetchone()[0] == 0


def test_reorder_stage_delivered_without_nodes_and_copy_rollback(fulfil_env):
    e = fulfil_env
    delivered = e.deal('delivered')
    real = e.deals._write
    def fail_item(db, op, *args):
        if op == 'item':
            raise RuntimeError('synthetic failure')
        return real(db, op, *args)
    with e.store.read() as db:
        before = db.execute('SELECT COUNT(*) FROM deals').fetchone()[0]
    with patch.object(e.deals, '_write', side_effect=fail_item), pytest.raises(RuntimeError):
        e.write('reorder', delivered)
    with e.store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM deals').fetchone()[0] == before
    assert e.write('reorder', delivered)['id']


@pytest.mark.parametrize('resource', ['payments', 'issues', 'fulfilment', 'issue_draft'])
def test_unknown_ids(fulfil_env, resource):
    with pytest.raises(v.Missing):
        fulfil_env.service.read(resource, row_id=999999)


def test_missing_and_old_database_gets_do_not_migrate(tmp_path):
    path = tmp_path / 'absent.sqlite3'
    service = Fulfil(path)
    assert service.read('board')['items'] == [] and not path.exists()
    old = Store(tmp_path / 'old.sqlite3')
    did = Deals(store=old, lookup=lambda key: None).write('create', None, {'stage': 'inquiry'}, 'fictional-owner')['id']
    # Legacy won data is injected directly; new public creates cannot bypass payment.
    with old.write() as db:
        db.execute("UPDATE deals SET stage='paid',status='won',won_at=created_at WHERE id=?", (did,))
    service = Fulfil(store=old)
    with old.read() as db:
        before = list(db.execute('SELECT * FROM schema_migrations'))
    assert service.read('payments', row_id=did) == dict(items=[], summary=[])
    assert service.read('issues', row_id=did)['items'] == []
    assert len(service.read('fulfilment', row_id=did)['items']) == 8
    with old.read() as db:
        assert list(db.execute('SELECT * FROM schema_migrations')) == before


def test_whole_flow_never_sends_and_audits_only_field_names(fulfil_env):
    e = fulfil_env
    with patch('console.outbox.store.Store._write', side_effect=AssertionError('send')) as outbox, \
         patch('console.outbox.bridge_client.local_post', side_effect=AssertionError('channel')) as channels:
        pid = e.write('payment_create', kind='deposit', amount_fen='500', currency='USD', received_on='2030-01-01', method='wire')['id']
        e.write('payment_confirm', pid, confirm='1')
        e.write('node_update', child='booking', status='doing', note='FICTIONAL-PRIVATE-NOTE')
        iid = e.write('issue_create', kind='feedback', description='FICTIONAL-PRIVATE-ISSUE')['id']
        e.write('issue_note', iid, note='FICTIONAL-PRIVATE-HISTORY')
        e.service.read('issue_draft', row_id=iid)
        e.write('node_update', child='signoff', status='done')
        e.service.read('reorder_leads')
        e.write('reorder')
        outbox.assert_not_called()
        channels.assert_not_called()
    with e.store.read() as db:
        audit = [dict(r) for r in db.execute("SELECT * FROM audit WHERE table_name IN ('deal_payments','fulfilment_nodes','deal_issues','deal_issue_notes')")]
    assert audit and all(a['who'] == 'fictional-owner' and a['at'] for a in audit)
    assert 'FICTIONAL-PRIVATE' not in json.dumps(audit)


def test_confirmed_deposit_can_advance_manually_without_shipping_on_inspection(fulfil_env):
    e = fulfil_env
    did = e.deal('inquiry')
    pid = e.write('payment_create', did, kind='deposit', amount_fen='100', currency='USD',
                  received_on='2030-01-01', method='wire')['id']
    e.write('payment_confirm', pid, confirm='1')
    paid = e.deals.read('detail', row_id=did)
    e.write('node_update', did, 'inspection', status='done', inspection_result='pass')
    progress = e.service.read('fulfilment', row_id=did)
    suggestion = next(s for s in progress['suggestions'] if s['stage'] == 'shipped')
    assert '不代表已发货' in suggestion['reason'] and '由人确认' in suggestion['reason']
    assert e.deals.read('detail', row_id=did)['stage'] == 'paid'
    for stage in ('inspection', 'shipped', 'delivered', 'aftersales'):
        e.deals.write('update', did, {'stage': stage}, 'fictional-operator')
        current = e.deals.read('detail', row_id=did)
        assert current['stage'] == stage and current['status'] == 'won' and current['won_at'] == paid['won_at']


def test_reorder_preview_scopes_feedback_suppliers_and_copies_only_customer_products(fulfil_env):
    e = fulfil_env
    e.quote()
    e.deals.write('participant', e.did, {'party_id': str(e.supplier), 'role': 'supplier'}, 'fictional-owner')
    iid = e.write('issue_create', kind='feedback', description='虚构客户希望改善外箱', product_id=str(e.product),
                  responsible='supplier', party_id=str(e.supplier))['id']
    e.write('issue_update', iid, status='resolved', resolution='虚构外箱方案待返单时复核')
    e.write('issue_note', iid, note='FICTIONAL-PRIVATE-HANDLING')
    foreign = e.deals.write('create', None, {'title': '另一个虚构客户案子'}, 'fictional-owner')['id']
    e.write('issue_create', foreign, kind='feedback', description='FICTIONAL-OTHER-CUSTOMER')
    e.write('node_update', child='signoff', status='done', signed_on='2029-11-02')
    before = e.deals.read('detail', row_id=e.did)
    lead = e.service.read('reorder_leads')['items'][0]
    assert lead['suppliers'] == [dict(id=e.supplier, name='虚构供应商星河', source_label='本案供应商参与方，需核对实际供货方')]
    assert lead['feedback'] == [dict(id=iid, kind='feedback', kind_label='客户意见建议', status='resolved',
        description='虚构客户希望改善外箱', resolution='虚构外箱方案待返单时复核', product_id=e.product, party_id=e.supplier)]
    assert lead['products'][0]['sell_unit_minor'] == 3500
    assert e.deals.read('detail', row_id=e.did) == before
    for forbidden in ('cost_unit', '1379', '4321', 'FICTIONAL-OTHER-CUSTOMER', 'FICTIONAL-PRIVATE-HANDLING'):
        assert forbidden not in json.dumps(lead)
    copied = e.write('reorder')['id']
    detail = e.deals.read('detail', row_id=copied)
    assert detail['stage'] == 'inquiry' and detail['status'] == 'open'
    assert all(p['role'] == 'customer' for p in detail['participants'])
    assert detail['items'][0]['target_price'] is None
    assert e.service.read('payments', row_id=copied)['items'] == []
    assert e.service.read('issues', row_id=copied)['items'] == []
    assert all(n['id'] is None for n in e.service.read('fulfilment', row_id=copied)['items'])


def test_reorder_preview_current_product_supplier_is_labeled_and_old_issues_stay_empty(fulfil_env):
    e = fulfil_env
    e.write('node_update', child='signoff', status='done', signed_on='2029-11-02')
    lead = e.service.read('reorder_leads')['items'][0]
    assert lead['suppliers'] == [dict(id=e.supplier, name='虚构供应商星河', source_label='产品档案当前供应商，需核对上次实际供货方')]
    assert lead['feedback'] == [] and lead['products'][0]['sell_unit_minor'] is None
