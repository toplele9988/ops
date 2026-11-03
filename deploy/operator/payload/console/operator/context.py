"""Read-only archive/inbox projection. No guessed archive columns or raw SQL writes."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from console.archive.store import Store as ArchiveStore
from console.inbox.model import decode_key
from console.kb.policy import allowed
from console.customers.identity import projected_parties
from .facts import usable


class ArchiveContext:
    def __init__(self, archive_path, inbox, *, hub=None, profiles=None, quote_policy=None, documents=None, language=None):
        self.archive = ArchiveStore(archive_path, target_version=9)
        self.inbox, self.hub = inbox, hub
        self.profiles = profiles or (lambda: {})
        self.quote_policy = quote_policy or (lambda deal: None)
        self.documents = documents or (lambda deal, key: {})
        self.language = language

    def snapshot(self, event, now):
        key = event['conversation_key']
        profiles = self.profiles()
        with self.archive.read() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            def rows(table, where='1', args=()):
                return [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE '+where, args)] if table in tables else []
            owner = rows('identities', 'conversation_key=?', (key,))
            if not owner:
                raise ValueError('identity_required')
            pid = owner[0]['party_id']
            links = rows('deal_conversations', 'conversation_key=? AND party_id=?', (key, pid))
            deals = [d for link in links for d in rows('deals', "id=? AND status IN ('open','won')", (link['deal_id'],))]
            if event.get('deal_id') is not None:
                deals = [d for d in deals if d['id'] == event['deal_id']]
                if not deals:
                    raise ValueError('deal_not_active_or_not_linked')
            if len(deals) > 1:
                raise ValueError('deal_ambiguous')
            deal = deals[0] if deals else None
            parties = projected_parties(db)
            convs = rows('deal_conversations', 'deal_id=?', (deal['id'],)) if deal else [dict(conversation_key=key, party_id=pid)]
            contacts = {}
            for link in convs:
                party = parties.get(link['party_id'])
                if not party or party['status'] != 'active':
                    continue
                identity = rows('identities', 'conversation_key=? AND party_id=?', (link['conversation_key'], link['party_id']))
                if not identity:
                    continue
                ck = link['conversation_key']
                channel = decode_key(ck, 'conversation')[0]
                subtype = {'customer': 'customer_other', 'supplier': 'supplier_product',
                           'forwarder': 'supplier_logistics', 'vendor': 'supplier_support'}[party['kind']]
                override = profiles.get(ck, {})
                # A profile can refine a type, never turn a customer into a supplier.
                typ = override.get('type', subtype)
                permitted = {'customer': ('customer_project','customer_wholesale','customer_end','customer_other'),
                             'supplier': ('supplier_product',), 'forwarder': ('supplier_logistics',),
                             'vendor': ('supplier_support',)}[party['kind']]
                if typ not in permitted:
                    typ = subtype
                contacts[ck] = dict(dict(timezone=party.get('timezone'), tone='professional'), **override)
                contacts[ck].update(key=ck, channel=channel, party_id=party['id'], name=party['name'],
                                    type=typ, quota_key=ck)
            items = rows('deal_items', 'deal_id=?', (deal['id'],)) if deal else []
            ids = {i['product_id'] for i in items if i['product_id']}
            products = [p for p in rows('products') if p['id'] in ids]
            merged_products = {p['product_id'] for p in rows('product_merges')}
            products = [p for p in products if p['id'] not in merged_products]
            sources = rows('product_sources')
            for product in products:
                refs = [r['ref'] for r in sources if r['product_id'] == product['id']]
                product['source_refs'] = refs
                states = json.loads(product.get('field_state_json') or '{}')
                # Archive field states are field -> {state, source, at}.
                from console.quotes.freight import PACKING
                product['packing_confirmed'] = bool(refs) and all(states.get(k, {}).get('state') in ('confirmed', 'manual') for k in PACKING)
            offers = [o for o in rows('offers') if o['product_id'] in ids]
            participant_ids = {link['party_id'] for link in convs}
            freight = [f for f in rows('freight_quotes') if f['forwarder_id'] in participant_ids]
            destinations = rows('deal_operation_context', 'deal_id=?', (deal['id'],)) if deal else []
            payments = rows('deal_payments', "deal_id=? AND status='confirmed' AND qualifies=1 AND kind!='refund'", (deal['id'],)) if deal else []
        histories = {ck: self.inbox.detail(ck, now) for ck in contacts}
        if event.get('message_id') and event['kind'] == 'inbound' and hasattr(self.inbox, 'detail_at'):
            histories[key] = self.inbox.detail_at(key, event['message_id'], now)
        for ck, contact in contacts.items():
            if not contact.get('language'):
                language = self.language(ck) if self.language else None
                if language and (language.get('locked') or language.get('confidence', 0) >= .8):
                    contact['language'] = language.get('native_language')
        history = histories.get(key)
        messages = (history or {}).get('messages', [])[-20:]
        customer_id = parties.get((deal or {}).get('customer_party_id'), {}).get('id')
        customer_keys = [ck for ck, c in contacts.items() if c['type'].startswith('customer_') and c['party_id'] == customer_id]
        if deal and any(c['type'].startswith('customer_') and c['party_id'] != customer_id for c in contacts.values()):
            raise ValueError('multiple_customers_in_deal')
        # Reply goes to the customer's original inquiry identity, never an arbitrary account.
        customer_key = key if key in customer_keys else (deal or {}).get('source_conversation_key')
        if customer_key not in customer_keys:
            customer_key = customer_keys[0] if len(customer_keys) == 1 else None
        documents = self.documents(deal, customer_key) if deal else {}
        facts = [d['fact'] for d in documents.values() if d.get('ready') and d.get('fact')]
        from .evidence import product_facts
        facts.extend(product_facts(products, offers, customer_key, now))
        for item in items:
            if not item.get('qty'):
                continue
            p = next((p for p in products if p['id'] == item.get('product_id') and p['status'] == 'confirmed' and p['source_refs']), None)
            if p:
                facts.append(dict(id='requirement:'+str(item['id']), kind='requirement',
                    text=f"{item['qty']} × {p['name']}", status='confirmed', source_ref=f"deal_item:{item['id']}",
                    observed_at=(deal or {})['updated_at'], public=True, is_cost_price=False, confidential=False,
                    customer_key=customer_key))
        if destinations:
            facts.append(dict(id='destination:'+str(deal['id']), kind='requirement',
                text='Destination / 目的地: '+destinations[0]['destination_country'], status='confirmed',
                source_ref='deal_operation_context:'+str(deal['id']), observed_at=deal['updated_at'],
                public=True, is_cost_price=False, confidential=False, customer_key=customer_key))
        return dict(contact_key=key, contacts=contacts, parties=[parties[i] for i in participant_ids] if deal else [parties[pid]],
                    deal=deal, items=items, products=products, documents=documents,
                    offers=offers, freight_quotes=freight, facts=facts, messages=messages, histories=histories,
                    customer_key=customer_key, payment_confirmed=bool(any(p.get('confirmed_by') and p.get('confirmed_at') and p['confirmed_by'] not in ('operator_auto','model_auto') for p in payments) and deal and deal['status'] == 'won' and deal['stage'] in ('paid','inspection','shipped','delivered','aftersales','reorder')),
                    destination_country=destinations[0]['destination_country'] if destinations else None,
                    quote_policy=self.quote_policy(deal) if deal else None)

    def due_events(self, now):
        """Stable task ID/deadline and last-outbound ID form replay-safe event IDs."""
        result = []
        with self.archive.read() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'deals', 'deal_conversations', 'deal_tasks'} <= tables:
                return result
            rows = list(db.execute("SELECT t.id,t.deal_id,t.due,c.conversation_key FROM deal_tasks t "
                "JOIN deals d ON d.id=t.deal_id JOIN deal_conversations c ON c.deal_id=d.id "
                "WHERE t.status='open' AND t.due IS NOT NULL AND d.status='open' ORDER BY t.id"))
            for row in rows:
                try:
                    due = datetime.fromisoformat(row['due'].replace('Z', '+00:00'))
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                    if due.timestamp() <= now:
                        result.append(dict(id=f"task:{row['id']}:{row['due']}:{row['conversation_key']}", kind='task_due',
                                           deal_id=row['deal_id'], conversation_key=row['conversation_key']))
                except ValueError:
                    continue
            links = [dict(r) for r in db.execute("SELECT c.* FROM deal_conversations c JOIN deals d ON d.id=c.deal_id WHERE d.status='open'")]
        for link in links:
            conv = self.inbox.detail(link['conversation_key'], now)
            messages = (conv or {}).get('messages', [])
            last = next((m for m in reversed(messages) if m['role'] != 'unknown'), None)
            if last and last['role'] == 'seller' and now-last['sent_at'] >= 48*3600:
                result.append(dict(id=f"timeout:{link['conversation_key']}:{last['sent_at']}", kind='reply_timeout',
                                   deal_id=link['deal_id'], conversation_key=link['conversation_key']))
        return result


def model_context(context, hub, today):
    """Customer prompts never contain supplier cost or other buyer histories."""
    contact = context['contacts'].get(context['contact_key'], {})
    customer = contact.get('type', '').startswith('customer_')
    purpose = 'customer_reply' if customer else 'product_facts'
    query = '\n'.join(m['text'] for m in context.get('messages', [])[-2:])[:500].strip()
    kb = []
    if hub and query:
        scope = None
        if customer:
            channel, account, identity = decode_key(context['contact_key'], 'conversation')
            scope = hub.index.conversation_scope(channel, account, identity)
        result = hub.search(query, purpose, 8, conversation_scope=scope)
        for item in result.get('items', []):
            if allowed(item['family'], purpose, confidential=item.get('confidential', True),
                       is_cost_price=item.get('is_cost_price', True),
                       source_scope=item.get('source_scope', ''), conversation_scope=scope):
                kb.append(item)
    return dict(contact=contact, deal={k: context['deal'].get(k) for k in ('id','stage','status')} if context.get('deal') else None,
                messages=deepcopy(context.get('messages', [])[-20:]), kb=kb, purpose=purpose,
                facts=[f for f in context.get('facts', []) if usable(f, contact, today)])
