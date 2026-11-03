"""Local-only metadata projection of the actual inbound/WTAPI inbox schemas.

No media tokens, channel client, network fetch or source write. Rich messages
are not needed to decide whose turn it is. The original inbox keeps the detail.
"""
from collections import Counter, defaultdict
import json

from console.inbox.model import aggregate_messages, make_key, waiting_state
from console.overview.safety import database


def alibaba(path, now):
    with database(path, 1) as db:
        conversations = aggregate_messages((r[0], r[1]) for r in db.execute('SELECT id,payload FROM inbound ORDER BY id'))
    return {key: dict(c, counterpart_role='customer', **waiting_state(c['messages'], now)[0])
            for key, c in conversations.items()}


def wechat(path, now):
    with database(path, 1) as db:
        columns = lambda table: {r[1] for r in db.execute('PRAGMA table_info(' + table + ')')}
        msg, scope = columns('messages'), columns('message_scopes')
        if 'payload' not in msg or not {'message_id', 'scope_id', 'business_role'} <= scope:
            raise ValueError('missing schema')
        scoped = 'app_id' in msg and 'app_id' in scope
        app = 'app_id' if scoped else "''"
        roles, groups, counts, seen = defaultdict(Counter), defaultdict(list), defaultdict(Counter), set()
        for account, mid, role in db.execute('SELECT DISTINCT ' + app + ',message_id,business_role FROM message_scopes'):
            roles[(account, str(mid))][role] += 1
        for account, raw in db.execute('SELECT ' + app + ',payload FROM messages ORDER BY rowid'):
            try:
                p = json.loads(raw)
                group, mid, sender, own = (p.get(k) for k in ('group_id', 'message_id', 'sender_id', 'own_wxid'))
                if not all(isinstance(v, str) and v for v in (group, sender, own)) or not group.endswith('@chatroom') or mid is None:
                    continue
                at = p.get('created_at')
                if type(at) not in (int, float) or not 0 < at <= now:
                    continue
                key = (account, group, str(mid))
                if key in seen:
                    continue
                seen.add(key)
                text = p.get('text') or ('[附件待核对]' if p.get('attachments') else '')
                if not isinstance(text, str) or not text:
                    continue
                groups[group].append(dict(role='seller' if sender == own else 'buyer', text=text, sent_at=at))
                counts[group].update(roles[(account, str(mid))])
            except (ValueError, TypeError, AttributeError):
                continue
    result = {}
    for group, messages in groups.items():
        messages.sort(key=lambda m: m['sent_at'])
        role = min(counts[group], key=lambda r: (-counts[group][r], r)) if counts[group] else 'unclassified'
        key = make_key('conversation', 'wechat', '微信', group)
        result[key] = dict(key=key, channel='wechat', buyer_name='微信群 · ' + group[-4:], messages=messages,
                           counterpart_role=role, **waiting_state(messages, now)[0])
    return result
