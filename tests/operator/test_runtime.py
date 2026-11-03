from datetime import date
from console.archive.store import insert
from console.operator.runtime import QuotePolicyFromRound, build_operator
from console.quotes.service import Quotes
from conftest import STAMP


def test_reuses_reviewed_quote_terms_and_refuses_omitted_extra_costs(system):
    s=system
    quotes=Quotes(store=s.archive,clock=lambda:s.now[0],today=lambda:date(2030,1,1))
    policy=QuotePolicyFromRound(quotes)
    assert policy({'id':1}) is None
    with s.archive.write() as db:
        insert(db,'quote_rounds',dict(id=1,deal_id=1,round_no=1,status='draft',currency='USD',
            fx_x10000=70000,fx_source='fictional-reviewed-fx',incoterm='EXW',target_margin_bp=2000,
            valid_until='2030-01-15',created_at=STAMP,updated_at=STAMP),'fixture')
        row=dict(db.execute('SELECT * FROM quote_rounds').fetchone())
    assert policy({'id':1}) is None
    quotes.state.save(quotes._state_key(row),dict(terms=dict(confirmed=True,confirmed_by='fictional-owner',confirmed_at=STAMP,
        payment='30_70',delivery_scope='Fictional EXW pickup',seller_name='Fictional Seller',seller_address='Fictional address')),
        'fictional-owner','terms',STAMP)
    assert policy({'id':1})['fx_x10000']==70000
    assert policy({'id':1})['source_ref'].startswith('quote_round:1:terms:')
    with s.archive.write() as db:
        insert(db,'quote_costs',dict(round_id=1,kind='vendor',manual=1,amount_fen=1000),'fixture')
    assert policy({'id':1}) is None


def test_runtime_factory_is_inert_until_explicit_event(system,tmp_path):
    s=system
    op, events=build_operator(operator_path=tmp_path/'factory.sqlite3',archive_path=s.archive.path,
        inbox=s.inbox,hub=None,quote_policy=lambda _:s.policy,model=s.model,bridge=s.bridge,clock=lambda:s.now[0])
    assert not op.store.path.exists() and events.operator is op
    assert op.read()['transport']=='fake'
