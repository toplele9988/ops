"""PI/order previews using the existing quote renderer and reviewed pricing terms.

These are durable operator drafts, never a payment confirmation or placed order.
An integrator may inject the existing Quotes document projection instead.
"""
from console.quotes.text import render, pi_preview
from .facts import locale
from .quote import calculate_quote
from .store import sha, dumps


def prepare_documents(context, today):
    documents = context.setdefault('documents', {})
    key = context.get('customer_key')
    if not key:
        return
    lang = locale(context['contacts'][key])
    quote, errors = calculate_quote(context, today)
    if errors or lang not in ('zh', 'en'):
        return
    policy = context['quote_policy']
    terms = {k: policy.get(k) for k in ('seller_name', 'seller_address', 'payment', 'delivery_scope')}
    round_row = dict(quote['round'], round_no=1)
    preview = pi_preview(round_row, quote['lines'], terms)
    ready = not preview['missing'] and terms['payment'] == '30_70'
    for kind in ('draft_pi', 'draft_order'):
        if kind in documents:
            continue
        title = {'draft_pi': {'zh': '形式发票草稿（未确认收款）', 'en': 'Proforma invoice draft (payment unconfirmed)'},
                 'draft_order': {'zh': '订单草稿（待确认）', 'en': 'Order draft (for confirmation)'}}[kind][lang]
        # Seller identity is taken only from the reviewed pricing policy; never bank details.
        text = title + '\n' + '\n'.join(str(terms[k] or '【待确认】') for k in ('seller_name', 'seller_address'))
        text += '\n' + render(round_row, quote['lines'], lang, terms)
        digest = sha(dumps([kind, quote['digest'], text, ready, key]))
        fact = dict(id=kind+':'+digest, kind='document', text=text, status='confirmed',
                    source_ref='operator_document:'+digest, observed_at=today,
                    valid_until=quote['round']['valid_until'], language=lang,
                    public=True, confidential=False, is_cost_price=False, customer_key=key,
                    dependencies=[policy['source_ref'], quote['digest']])
        documents[kind] = dict(ready=ready, fact=fact, source_hash=digest, missing=preview['missing'])
        context['facts'].append(fact)
