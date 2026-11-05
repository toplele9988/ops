"""Local private-chat projections. All source reads are explicit and read-only."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

ARCHIVE_PATH = Path('/var/lib/imgw/ops-app/archive.sqlite3')
CONTACT_PATH = Path('/var/lib/imgw/wechat/private-contact-directory-v2/contacts.sqlite3')
KINDS = frozenset(('customer', 'supplier', 'forwarder', 'vendor', 'unknown'))


def digest(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def account_key(app, own):
    return '微信私聊:' + digest(app + '\0' + own)


def scope_key(app, own, peer):
    return 'wechat-private:' + digest(app + '\0' + own + '\0' + peer)


def broad_role(role):
    return 'supplier' if role in ('forwarder', 'vendor') else 'unclassified' if role == 'unknown' else role


@contextmanager
def readonly(path):
    path = Path(path).resolve()
    # Static sources cannot create SHM files. Live WAL sources require existing SHM.
    wal = Path(str(path) + '-wal')
    live = wal.exists() and wal.stat().st_size > 0
    if live and not Path(str(path) + '-shm').exists():
        raise ValueError('private_source_shm_unavailable')
    db = sqlite3.connect(path.as_uri() + '?mode=ro' + ('' if live else '&immutable=1'), uri=True, timeout=1)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def contact_role(app, own, peer, *, archive_path=ARCHIVE_PATH, contact_path=CONTACT_PATH, fallback=''):
    """Confirmed identity wins; a name is only a rule hint, never an identity link."""
    from console.inbox.model import make_key
    key = make_key('conversation', 'wechat', account_key(app, own), peer)
    try:
        from console.customers.identity import bindings, projected_parties
        with readonly(archive_path) as db:
            party = bindings(db).get(key)
            if party is None:
                legacy = {p['id']: p for p in projected_parties(db).values() if p.get('conversation_ref') == key}
                party = next(iter(legacy.values())) if len(legacy) == 1 else None
        if party and party['kind'] in KINDS - {'unknown'}:
            return party['kind'], party['name'], 'confirmed_identity'
    except (OSError, ValueError, sqlite3.Error):
        pass
    name = fallback
    try:
        with readonly(contact_path) as db:
            row = db.execute('SELECT remark,nickname FROM contacts WHERE account_sha256=? AND contact_id=? '
                             'ORDER BY updated_at DESC LIMIT 1', (digest(app + '\0' + own), peer)).fetchone()
        if row:
            name = row['remark'] or row['nickname'] or name
    except (OSError, ValueError, sqlite3.Error):
        pass
    from console.directory.service import naming
    role, codes = naming(name or '')
    if len(codes) > 1 or role not in KINDS:
        role = 'unknown'
    return role, name or '未命名联系人', 'name_rule' if role != 'unknown' else 'unconfirmed'


def private_rows(db):
    db.row_factory = sqlite3.Row
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'wechat_private_messages', 'wechat_private_contacts'} <= tables:
        return []
    return db.execute('''SELECT m.*,p.own_wxid,p.peer_id,p.direction,p.scope_id,p.callback_sha256,
        c.business_role,c.display_name FROM wechat_private_messages p
        JOIN messages m ON m.app_id=p.app_id AND m.message_id=p.message_id
        JOIN wechat_private_contacts c ON c.app_id=p.app_id AND c.own_wxid=p.own_wxid AND c.peer_id=p.peer_id
        ORDER BY m.created_at,m.rowid''').fetchall()


def conversations(db, *, archive_path=ARCHIVE_PATH, contact_path=CONTACT_PATH, blocks=None, names=None):
    from console.channels.common import conversation
    grouped, labels = {}, {}
    for row in private_rows(db):
        try:
            p = json.loads(row['payload'])
            if (not isinstance(p, dict) or not isinstance(p.get('text'), str)
                    or not isinstance(row['created_at'], (int, float)) or row['created_at'] <= 0):
                continue
        except (ValueError, TypeError):
            continue
        ident = (row['app_id'], row['own_wxid'], row['peer_id'])
        if p.get('group_id') != row['peer_id'] or p.get('own_wxid') != row['own_wxid']:
            continue
        message = dict(role='seller' if row['direction'] == 'outbound' else 'buyer',
                       text=p['text'], sent_at=row['created_at'], sender_label='我方' if row['direction'] == 'outbound' else '对方',
                       message_id=row['message_id'])
        if blocks and p.get('attachments'):
            message['blocks'] = blocks(p, row['app_id'], row['message_id'])
        grouped.setdefault(ident, []).append(message)
        labels[ident] = row['display_name']
    result = {}
    if grouped and names is None:
        from console.names import NamesResolver
        state_path = db.execute('PRAGMA database_list').fetchone()[2]
        names = NamesResolver(state_path, contacts_path=contact_path).snapshot(state_db=db)
    for (app, own, peer), messages in grouped.items():
        role, name, basis = contact_role(app, own, peer, archive_path=archive_path,
                                       contact_path=contact_path, fallback=labels[app, own, peer])
        resolved = names.contact(peer, app=app, own=own, provided_name=name)
        country = names.country(peer, app=app, own=own)
        conv = conversation('wechat', '微信', account_key(app, own), peer, resolved.value, messages,
                            broad_role(role), is_group=False)
        conv.update(business_role=role, role_basis=basis, needs_classification=role == 'unknown',
                    country=country[0] if country else '', name_missing=not resolved.named, name_missing_reason=resolved.reason)
        result[conv['key']] = conv
    return result


def knowledge_hits(path, query, purpose, top_k, families=None, *, archive_path=ARCHIVE_PATH, contact_path=CONTACT_PATH):
    """Internal lexical supplement, including ready media text. Private data never enters customer drafts."""
    if purpose == 'customer_reply':
        return []
    from console.kb.text import tokens, snippet
    words = set(tokens(query))
    if not words:
        return []
    hits = []
    try:
        with readonly(path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            roles = {}
            for row in private_rows(db):
                ident = row['app_id'], row['own_wxid'], row['peer_id']
                if ident not in roles:
                    roles[ident] = contact_role(*ident, archive_path=archive_path, contact_path=contact_path,
                                               fallback=row['display_name'])[0]
                role = roles[ident]
                family = ('supplier_chat' if role in ('supplier', 'forwarder', 'vendor') else
                          'customer_conversation' if role == 'customer' else 'private_note')
                if purpose == 'product_facts' and family != 'supplier_chat' or families and family not in families:
                    continue
                body = json.loads(row['payload'])['text']
                for table in ('company_media', 'company_note_assets'):
                    if table in tables:
                        body += '\n' + '\n'.join(r[0] for r in db.execute(
                            'SELECT text FROM ' + table + " WHERE app_id=? AND message_id=? AND status='ready'",
                            (row['app_id'], row['message_id'])) if r[0])
                overlap = len(words & set(tokens(body)))
                if not overlap:
                    continue
                hits.append(dict(score=overlap, family=family, family_label='微信私聊', title='微信私聊原始记录',
                    source_type_label='微信私聊', date=datetime.fromtimestamp(row['created_at'], timezone.utc).isoformat(),
                    snippet=snippet(body, query, 300), source_id='wxp_' + digest(row['app_id'] + '\0' + row['message_id']),
                    confidential=True, is_cost_price=family == 'supplier_chat'))
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        return []
    hits.sort(key=lambda h: (-h['score'], h['source_id']))
    return [dict(h, rank=i) for i, h in enumerate(hits[:top_k], 1)]
