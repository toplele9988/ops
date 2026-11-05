"""Regression scenarios found while integrating the previous candidate with r11."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import uuid
import pytest

from console.archive.store import insert
from console.archive.validation import Conflict
from console.operator.context import model_context
from console.operator.events import Events
from console.operator.facts import guard, usable
from console.operator.store import dumps
from conftest import enable, switch, STAMP


def one(rows, kind):
    return next(r for r in rows if r['kind'] == kind)


def test_post_commit_hooks_run_parallel_then_continue_quote(system):
    s = system
    enable(s); enable(s, 1, 'forwarder')
    events = Events(s.op)
    actions = events.inbound(s.keys['customer'], 'fictional-message', deal_id=1)
    assert {c['conversation_key'] for c in s.bridge.calls} == {s.keys['supplier'], s.keys['forwarder']}
    assert one(actions, 'reply_customer')['state'] == 'pending'
    s.supplier(); events.supplier_reply(s.keys['supplier'], 'fictional-offer-1', deal_id=1)
    s.forwarder()
    quote = one(events.forwarder_reply(s.keys['forwarder'], 'fictional-freight-1', deal_id=1), 'quote_customer')
    assert quote['state'] == 'pending'
    public = '\n'.join(quote['data']['parts'])
    assert 'USD 75.32' in public
    assert not any(word in public for word in ('CNY', '100.00', 'supplier', 'profit'))
    before = len(s.bridge.calls)
    events.forwarder_reply(s.keys['forwarder'], 'fictional-freight-1', deal_id=1)
    assert len(s.bridge.calls) == before


@pytest.mark.parametrize('failure', ['unknown', 'exception'])
def test_uncertain_model_only_asks_owner(system, failure):
    s = system; enable(s)
    if failure == 'unknown':
        s.model.intent = 'uncertain'
    else:
        s.model.decide = lambda _: (_ for _ in ()).throw(TimeoutError())
    rows = s.op.handle(s.event())
    assert [r['kind'] for r in rows] == ['ask_owner']
    s.op.tick(); assert not s.bridge.calls
    assert s.op.handle(s.event()) == rows


@pytest.mark.parametrize('intent', ['accepted', 'payment_report'])
def test_supplier_cannot_accept_customer_order_or_payment(system, intent):
    system.model.intent = intent
    rows = system.op.handle(system.event(party='supplier'))
    assert [r['kind'] for r in rows] == ['ask_owner']


def test_closed_or_wrong_deal_does_not_create_another_deal(system):
    s = system
    with s.archive.write() as db:
        db.execute("UPDATE deals SET status='paused' WHERE id=1")
    assert [a['kind'] for a in s.op.handle(s.event())] == ['ask_owner']


def test_stop_clears_old_human_approval(system):
    s = system; enable(s)
    row = one(s.op.handle(s.event()), 'ask_supplier')
    s.op.write('approve', row['id'], {'operation_key':'approval-before-stop'}, 'fictional-owner')
    assert len(s.bridge.calls) == 1
    switch(s.op, 'master', 'enabled', False)
    switch(s.op, 'master', 'enabled', True)
    s.now[0] += 40
    assert s.op.dispatch(row['id'])['state'] == 'dry_run'
    assert len(s.bridge.calls) == 1


def test_cannot_edit_partially_sent_action(system):
    s = system; enable(s)
    row = one(s.op.handle(s.event()), 'ask_supplier'); s.op.dispatch(row['id'])
    with pytest.raises(Conflict):
        s.op.write('edit', row['id'], {'operation_key':'edit-partial-send', 'text':row['data']['parts'][0]}, 'fictional-owner')


def test_crash_after_reservation_is_quarantined(system):
    s = system; enable(s)
    row = one(s.op.handle(s.event()), 'ask_supplier')
    with s.op.store.write() as db:
        db.execute("INSERT INTO operator_attempts(action_id,part,contact_key,digest,idempotency_key,state,created_at) VALUES(?,0,?,'fictional-digest','fictional-crash','sending',?)", (row['id'], s.keys['supplier'], s.now[0]))
        db.execute("UPDATE operator_actions SET state='sending' WHERE id=?", (row['id'],))
    assert s.op.dispatch(row['id'])['state'] == 'unknown'
    assert not s.bridge.calls


def test_r11_merge_timezone_and_fresh_binding(system):
    s = system
    from console.customers.operations import install
    with s.archive.write() as db:
        install(db)
        insert(db, 'parties', dict(id=4, kind='customer', name='Fictional merged customer', status='active', created_at=STAMP, updated_at=STAMP), 'fixture')
        db.execute("INSERT INTO parties_ops_merges(source_id,target_id,at,who) VALUES(1,4,?,'fixture')", (STAMP,))
        db.execute("INSERT INTO parties_ops_profiles(party_id,updated_at,timezone) VALUES(4,?,'America/New_York')", (STAMP,))
    with s.op.store.write() as db:
        db.execute('DELETE FROM operator_profiles WHERE conversation_key=?', (s.keys['customer'],))
    s.source.language = lambda _: dict(native_language='en', locked=True)
    row = one(s.op.handle(s.event()), 'reply_customer')
    c = row['data']['context']['contacts'][s.keys['customer']]
    assert c['party_id'] == 4 and c['timezone'] == 'America/New_York' and c['language'] == 'en'
    with s.archive.write() as db:
        db.execute("UPDATE parties_ops_merges SET undone_at=?", (STAMP,))
    assert s.op.dispatch(row['id'])['state'] == 'blocked'


def test_due_task_and_unanswered_supplier_resume(system):
    s = system
    with s.archive.write() as db:
        insert(db, 'deal_tasks', dict(deal_id=1, title='Fictional overdue request', due='2029-12-31', status='open', stage='inquiry', created_at=STAMP), 'fixture')
    s.inbox.messages[s.keys['supplier']] = [dict(role='seller', text='Fictional request', sent_at=s.now[0]-49*3600)]
    s.op.tick()
    with s.op.store.read() as db:
        kinds = {__import__('json').loads(r[0])['kind'] for r in db.execute('SELECT payload FROM operator_events')}
        before = db.execute('SELECT count(*) FROM operator_events').fetchone()[0]
    assert kinds == {'task_due', 'reply_timeout'}
    s.op.tick()
    with s.op.store.read() as db:
        assert db.execute('SELECT count(*) FROM operator_events').fetchone()[0] == before


def test_customer_kb_scope_and_label_filter(system):
    s = system
    calls = []
    def search(query, purpose, top, **kw):
        calls.append((purpose, kw))
        return dict(items=[dict(family=family, text=label, confidential=private, is_cost_price=cost)
            for family, label, private, cost in [('supplier','cost-secret',False,True), ('technical','private-secret',True,False),
                ('customer_conversation','other-buyer-secret',False,False), ('technical','public-spec',False,False)]])
    hub = SimpleNamespace(index=SimpleNamespace(conversation_scope=lambda *args:'fictional-exact-scope'), search=search)
    ctx = s.source.snapshot(s.event(), s.now[0])
    result = model_context(ctx, hub, s.op.day())
    assert calls == [('customer_reply', {'conversation_scope':'fictional-exact-scope'})]
    assert [f['text'] for f in result['kb']] == ['public-spec']
    assert 'secret' not in dumps(result)


def test_confirmed_spec_and_lead_sources_used_without_cost(system):
    s = system; s.supplier()
    with s.archive.write() as db:
        db.execute('UPDATE products SET wattage=80,field_state_json=?', (dumps({'wattage':dict(state='confirmed',source='fictional-datasheet',at=STAMP)}),))
        db.execute('UPDATE offers SET lead_days_min=7,lead_days_max=9')
    s.model.intent = 'routine'; s.model.fact_ids = ['product:1:wattage:en','lead:1:en']
    row = one(s.op.handle(s.event()), 'reply_customer')
    assert '80.0 W' in ' '.join(row['data']['parts']) and '7–9 days' in ' '.join(row['data']['parts'])
    enable(s, 2, 'customer')
    with s.archive.write() as db:
        db.execute('UPDATE products SET wattage=90')
    assert s.op.dispatch(row['id'])['state'] == 'blocked'


@pytest.mark.parametrize('language', ['zh','en','es','fr','pt'])
def test_language_and_tone_follow_profile(system, language):
    s = system
    with s.op.store.write() as db:
        profile = s.op.profiles_in(db)[s.keys['customer']]
        profile.update(language=language, tone='friendly')
        db.execute('UPDATE operator_profiles SET value=? WHERE conversation_key=?', (dumps(profile), s.keys['customer']))
    row = one(s.op.handle(s.event()), 'reply_customer')
    assert row['state'] == 'dry_run'
    from console.operator.facts import opening
    assert row['data']['parts'] == [opening('reply_customer', profile)]


def test_pi_order_generated_and_revalidated(system):
    s = system; s.supplier(); s.forwarder()
    s.policy.update(seller_name='Fictional Seller',seller_address='Fictional address',payment='30_70')
    s.model.intent = 'accepted'
    rows = s.op.handle(s.event())
    for kind in ('draft_pi', 'draft_order'):
        row = one(rows, kind)
        assert row['state'] == 'dry_run' and row['data']['document_ready']
        text = '\n'.join(row['data']['parts'])
        assert 'USD' in text and '30%' in text and '70%' in text
        assert 'CNY' not in text and '100.00' not in text
    s.policy['seller_name'] = 'Fictional changed seller'
    assert s.op.dispatch(one(rows,'draft_pi')['id'])['state'] == 'blocked'


def test_missing_document_terms_never_auto_advance(system):
    s = system; s.supplier(); s.forwarder(); s.model.intent = 'accepted'
    rows = s.op.handle(s.event())
    assert one(rows,'draft_pi')['state'] == 'blocked'
    assert not any(a['kind']=='advance_deal' for a in rows)


def test_payment_wakeup_requires_record_and_human_actor(system):
    s = system
    rows = s.op.handle(s.event('false-payment','payment_confirmed'))
    assert [r['kind'] for r in rows] == ['ask_owner']
    from console.fulfil.service import Fulfil
    fulfil = Fulfil(store=s.archive, clock=lambda:s.now[0])
    payment = fulfil.write('payment_create', 1, dict(kind='full', amount_fen='10000', currency='USD', received_on='2030-01-01', method='wire', evidence='fictional bank evidence'), 'fictional-owner')
    fulfil.write('payment_confirm', payment['id'], dict(confirm='1',qualifies='1'), 'fictional-owner')
    rows = s.op.handle(s.event('human-payment','payment_confirmed'))
    assert one(rows,'schedule_shipping')['state'] == 'dry_run'
    assert one(rows,'shipping')['level'] == 3
    from console.operator.domain import DomainActions
    domain = DomainActions(fulfil.deals, fulfil)
    payload = one(rows,'schedule_shipping')['data']['payload']
    domain.apply(kind='schedule_shipping',payload=payload,operation_key='fictional-nodes-1',actor='fictional-owner')
    fulfil.write('node_update', 1, {'status':'doing'}, 'fictional-owner', child_id='booking')
    domain.apply(kind='schedule_shipping',payload=payload,operation_key='fictional-nodes-2',actor='fictional-owner')
    assert next(n for n in fulfil.read('fulfilment',row_id=1)['items'] if n['node']=='booking')['status']=='doing'


def test_no_source_or_other_buyer_fact_is_not_usable(system):
    contact = system.source.snapshot(system.event(),system.now[0])['contacts'][system.keys['customer']]
    base = dict(id='x',kind='specification',text='800 W',status='confirmed',source_ref='fictional-sheet',observed_at=STAMP,
                public=True,is_cost_price=False,confidential=False)
    for changes in ({'source_ref':''}, {'observed_at':'2099-01-01'}, {'customer_key':'another-buyer'}, {'confidential':None}, {'is_cost_price':None}):
        assert not usable(dict(base,**changes),contact,system.op.day())


@pytest.mark.parametrize('level', [2,3])
@pytest.mark.parametrize('bits', range(8))
def test_customer_level_matrix(system, level, bits):
    s=system
    if level==3:
        s.supplier(); s.forwarder()
    else:
        s.model.intent='routine'
    switch(s.op,'master','enabled'); switch(s.op,'master','dry_run',False)
    switch(s.op,'levels',str(level),bool(bits&1))
    switch(s.op,'types',f'{level}:customer_wholesale',bool(bits&2))
    switch(s.op,'contacts',f'{level}:'+s.keys['customer'],bool(bits&4))
    row=one(s.op.handle(s.event()),'quote_customer' if level==3 else 'reply_customer')
    assert row['state']==('ready' if bits==7 else 'pending')
    s.op.dispatch(row['id'])
    assert len(s.bridge.calls)==int(bits==7)


def test_profile_note_is_sourced_and_idempotent(system):
    s=system; s.model.intent='profile_update'
    s.inbox.messages[s.keys['customer']]=[dict(role='buyer',text='Our fictional company address has changed.',sent_at=s.now[0])]
    event=dict(s.event(),message_id='fictional-address-change')
    row=one(s.op.handle(event),'update_profile')
    assert row['state']=='dry_run' and row['level']==3
    from console.deals.service import Deals
    from console.operator.domain import DomainActions
    domain=DomainActions(Deals(store=s.archive,lookup={},clock=lambda:s.now[0]),None)
    values=dict(kind='update_profile',payload=row['data']['payload'],operation_key='fictional-profile-note',actor='fictional-owner')
    domain.apply(**values); domain.apply(**values)
    with s.archive.read() as db:
        rows=list(db.execute('SELECT summary FROM parties_ops_events'))
        assert len(rows)==1 and 'fictional-address-change' in rows[0][0]


def test_alibaba_real_event_never_enters_fake_transport(system):
    from console.inbox.model import make_key
    s=system; key=make_key('conversation','alibaba','fictional-store-6','fictional-buyer')
    with s.archive.write() as db:
        db.execute("UPDATE identities SET conversation_key=?,channel='alibaba' WHERE party_id=1",(key,))
        db.execute('UPDATE deal_conversations SET conversation_key=? WHERE party_id=1',(key,))
        db.execute('UPDATE deals SET source_conversation_key=?',(key,))
    with s.op.store.write() as db:
        db.execute('UPDATE operator_profiles SET conversation_key=? WHERE conversation_key=?',(key,s.keys['customer']))
    switch(s.op,'master','enabled');switch(s.op,'master','dry_run',False)
    s.model.intent='routine'
    row=one(s.op.handle(dict(id='fictional-ali',kind='inbound',conversation_key=key,deal_id=1)),'reply_customer')
    result=s.op.write('approve',row['id'],dict(operation_key='fictional-ali-approval'),'fictional-owner')
    assert result['action']['reason']=='channel_unavailable' and not s.bridge.calls


def test_unlinked_conversation_keeps_one_owner_question(system):
    s = system
    key = '["conversation","wechat","fictional","unlinked-room"]'
    import base64
    key = base64.urlsafe_b64encode(key.encode()).decode().rstrip('=')
    first = s.op.handle(dict(id='unlinked-1', kind='inbound', conversation_key=key))
    second = s.op.handle(dict(id='unlinked-2', kind='inbound', conversation_key=key))
    third = s.op.handle(dict(id='unlinked-3', kind='inbound', conversation_key=key))
    assert [a['kind'] for a in first] == ['ask_owner']
    assert first[0]['data']['payload']['reason'] == 'identity_required'
    assert second[-1]['id'] == third[-1]['id'] == first[0]['id']
    assert third[-1]['data']['repeats'] == 3
    assert not s.model.calls
    with s.op.store.read() as db:
        rows = db.execute("SELECT count(*) FROM operator_actions WHERE contact_key=?", (key,)).fetchone()[0]
    assert rows == 1
    # Once the owner closes it, the next message opens a fresh question.
    with s.op.store.write() as db:
        db.execute("UPDATE operator_actions SET state='rejected' WHERE id=?", (first[0]['id'],))
    fourth = s.op.handle(dict(id='unlinked-4', kind='inbound', conversation_key=key))
    assert fourth[0]['id'] != first[0]['id']


def test_collapse_script_merges_existing_owner_questions(system, tmp_path):
    import base64, importlib.util, pathlib
    s = system
    key = base64.urlsafe_b64encode(b'["conversation","wechat","fictional","old-room"]').decode().rstrip('=')
    # Simulate the pre-fix ledger: several open owner questions on one conversation.
    with s.op.store.write() as db:
        for i in range(4):
            db.execute("INSERT INTO operator_actions(event_id,kind,contact_key,level,state,reason,due_at,dry_run,data,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (f'old-{i}', 'ask_owner', key, 0, 'pending', 'owner_required', None, 1,
                       '{"kind":"ask_owner","parts":[],"fact_ids":[],"payload":{"reason":"identity_required"}}', 1, 1))
    root = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('collapse', root/'deploy/operator/collapse_owner_questions.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.collapse(s.op.store.path)['merged'] == 3
    result = mod.collapse(s.op.store.path, apply=True, backup=str(tmp_path/'backup.sqlite3'))
    assert result['open_after'] == result['open_before'] - 3 and (tmp_path/'backup.sqlite3').exists()
    shown = [a for a in s.op.read()['actions'] if a['contact_key'] == key]
    assert len(shown) == 1 and shown[0]['data']['repeats'] == 4
    # New messages keep merging into the surviving question.
    again = s.op.handle(dict(id='old-new', kind='inbound', conversation_key=key))
    assert again[-1]['id'] == shown[0]['id'] and again[-1]['data']['repeats'] == 5


def test_supplier_group_without_deal_stays_quiet_and_skips_model(system):
    from console.inbox.model import make_key
    s = system
    key = make_key('conversation', 'wechat', 'fictional-account', 'supplier-group-no-deal')
    s.inbox.messages[key] = [dict(role='buyer', text='Fictional price sheet attached.', sent_at=1893492000)]
    with s.archive.write() as db:
        insert(db, 'parties', dict(id=9, kind='supplier', name='Fictional quiet supplier', status='active',
                                   created_at='2030-01-01T10:00:00+00:00', updated_at='2030-01-01T10:00:00+00:00'), 'fixture')
        insert(db, 'identities', dict(party_id=9, conversation_key=key, channel='wechat',
                                      created_at='2030-01-01T10:00:00+00:00', created_by='fixture'), 'fixture')
    assert s.op.handle(dict(id='quiet-1', kind='inbound', conversation_key=key)) == []
    assert not s.model.calls
    with s.op.store.read() as db:
        assert db.execute('SELECT count(*) FROM operator_actions WHERE contact_key=?', (key,)).fetchone()[0] == 0
        assert db.execute("SELECT state FROM operator_events WHERE id='quiet-1'").fetchone()[0] == 'complete'
