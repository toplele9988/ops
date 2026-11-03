"""Buyer-name grouping is a convenience, not a verified customer identity."""
from console.inbox.model import last_message, make_key


FIELDS = ('key', 'buyer_name', 'country', 'channel', 'account', 'n_conversations',
          'n_messages', 'first_seen', 'last_seen', 'waiting_reply')


def aggregate_customers(conversations):
    customers = {}
    for conv in conversations:
        role = conv.get('counterpart_role', 'customer')
        if (role not in ('customer', 'unclassified') or
                (conv.get('is_group') and not (conv['channel'] == 'wechat' and role == 'customer'))):
            continue
        key = make_key('customer', conv['channel'], conv['account'], conv['buyer_name'])
        customer = customers.setdefault(key, dict(key=key, buyer_name=conv['buyer_name'],
            country='', channel=conv['channel'], account=conv['account'], n_conversations=0,
            n_messages=0, first_seen=conv['first_seen'], last_seen=conv['last_seen'],
            waiting_reply=False, conversations=[]))
        customer['first_seen'] = min(customer['first_seen'], conv['first_seen'])
        if conv['last_seen'] >= customer['last_seen']:
            customer['country'] = conv['country']
        customer['last_seen'] = max(customer['last_seen'], conv['last_seen'])
        customer['n_conversations'] += 1
        customer['n_messages'] += conv['n_messages']
        customer['waiting_reply'] |= conv['waiting_reply']
        customer['conversations'].append(dict(key=conv['key'], last=last_message(conv),
            n_messages=conv['n_messages'], waiting_reply=conv['waiting_reply']))
    for customer in customers.values():
        customer['conversations'].sort(key=lambda c: (-c['last']['sent_at'], c['key']))
    return customers


def public_customer(customer, detail=False):
    result = {k: customer[k] for k in FIELDS}
    if detail:
        result['conversations'] = customer['conversations']
    return result
