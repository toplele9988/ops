"""Owner-only source-backed inbox candidate. No server route, writes or sends.

The caller MUST obtain authenticated_owner from an already authenticated console
owner session. Never derive it from headers, query parameters, or request body.
The production factory reuses host's existing source authority, not usernames.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
import json
import math
import os
import re
import sqlite3
import threading
import time
import types
import unicodedata
from contextlib import closing
from datetime import datetime
from pathlib import Path

MAX_ROWS = 100000


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _display_name(value):
    if not isinstance(value,str):return None
    value=value.strip()
    if (not value or len(value)>128 or value.casefold() in ('unknown','null','none','buyer','客户','未知','-')
            or any(unicodedata.category(c).startswith('C') for c in value)):
        return None
    return value


def _key(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _ro(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def _cursor_encode(item):
    return base64.urlsafe_b64encode(_json(item).encode()).decode().rstrip('=')


def _cursor_decode(value):
    try:
        if not isinstance(value, str) or not 0 < len(value) <= 4096:
            raise ValueError
        result = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if (not isinstance(result, list) or len(result) != 2 or
                type(result[0]) not in (int, float) or not math.isfinite(result[0]) or
                not isinstance(result[1], str) or len(result[1]) != 64 or
                any(c not in '0123456789abcdef' for c in result[1])):
            raise ValueError
        return float(result[0]), result[1]
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('invalid_cursor') from None


def _epoch(iso, fallback):
    if iso:
        try:
            value = datetime.fromisoformat(iso.replace('Z', '+00:00'))
            if value.tzinfo is not None:
                return value.timestamp()
        except (ValueError, TypeError, OverflowError):
            pass
    return float(fallback)


class NativeAuthority:
    """No production adapter included; supply an explicit authority factory."""
    def __init__(self, authenticated_owner):
        raise RuntimeError('source_authority_adapter_not_included')


def _wechat_rows(db, g):
    sql = '''SELECT m.app_id,m.message_id,m.group_id,m.sender_id,m.created_at,m.payload,
                    s.own_wxid,s.profile_id,s.scope_id,s.business_role
             FROM messages m JOIN message_scopes s ON s.app_id=m.app_id AND s.message_id=m.message_id
             WHERE m.app_id=? AND m.group_id=? AND s.own_wxid=? AND s.profile_id=?
               AND s.scope_id=? AND s.business_role=? AND m.created_at>=? LIMIT ?'''
    rows = db.execute(sql, (g['app_id'], g['conversation'], g['account'], g['profile'],
                            g['scope'], g['role'], g['cutoff'], MAX_ROWS+1))
    for index, raw in enumerate(rows):
        if index >= MAX_ROWS:
            raise ValueError('source_scan_limit')
        r = dict(raw)
        try:
            payload = json.loads(r['payload'])
            keys = ('app_id', 'message_id', 'group_id', 'sender_id', 'created_at', 'own_wxid')
            if (not isinstance(payload, dict) or any(payload.get(k) != r[k] for k in keys)
                    or not isinstance(payload.get('text'), str)):
                continue
            stamp = float(r['created_at'])
            if not math.isfinite(stamp):
                continue
            yield {'message_id': r['message_id'], 'at': stamp,
                   'direction': 'outbound' if r['sender_id'] == r['own_wxid'] else 'inbound',
                   'text': payload['text'][:65536], 'truncated': len(payload['text']) > 65536, 'time_basis':'source'}
        except (ValueError, TypeError, KeyError, OverflowError):
            continue


def _add(by_key, ident, channel, account, conversation, scope, message, selected_key, anchor, page_limit, display_name=None, message_search=None):
    item=by_key.setdefault(ident,{'key':ident,'channel':channel,'account':account,
        'conversation_id':conversation,'scope':scope,'message_count':0,'latest_at':float('-inf'),'latest_time_basis':None,'messages':[], '_names':set(), '_message_matches':0})
    name=_display_name(display_name)
    if name and len(item['_names'])<2:item['_names'].add(name)
    item['message_count']+=1
    if message['at'] > item['latest_at']:
        item['latest_at']=message['at'];item['latest_time_basis']=message.get('time_basis')
    elif message['at'] == item['latest_at'] and message.get('time_basis') == 'received':
        item['latest_time_basis']='received'
    # A list request retains no message bodies. A detail request retains only
    # the requested conversation and at most one bounded page plus a sentinel.
    if selected_key != ident:
        return
    if message_search and message_search not in unicodedata.normalize('NFKC', message['text']).casefold():
        return
    item['_message_matches']+=1
    if anchor and not (message['at'] < anchor[0] or (message['at']==anchor[0] and message['message_id']>anchor[1])):
        return
    item['messages'].append(message)
    item['messages'].sort(key=lambda x:(-x['at'],x['message_id']))
    if len(item['messages'])>page_limit+1:
        item['messages'].pop()


def _assemble(runtime, *, selected_key=None, anchor=None, page_limit=30, message_search=None):
    """Stream approved sources. Never log or return rejected raw records."""
    groups, by_key = [], {}
    states = {'wechat': {'status': 'unavailable'}, 'alibaba': {'status': 'unavailable'},
              'whatsapp': {'status': 'unavailable'}, 'website': {'status': 'not_checked'},
              'gmail': {'status': 'not_checked'}}
    try:
        with closing(_ro(runtime.wechat_path)) as db:
            groups = runtime.group_grants(db)
            for g in groups:
                ident = _key(['wechat', g['app_id'], g['account'], g['profile'], g['source_scope'], g['conversation']])
                for message in _wechat_rows(db, g):
                    _add(by_key,ident,'wechat',g['account'],g['conversation'],g['source_scope'],message,selected_key,anchor,page_limit,display_name=g.get('display_name'),message_search=message_search)
            runtime.revalidate(db, groups)
        states['wechat'] = {'status': 'partial' if len(groups) < runtime.group_total else 'ok',
                            'authorized_groups':len(groups), 'other_groups_unavailable':runtime.group_total-len(groups)}
    except (ValueError, PermissionError, OSError, sqlite3.Error, KeyError, TypeError):
        by_key = {k:v for k,v in by_key.items() if v['channel'] != 'wechat'}
    if runtime.ali_auth is not None:
        try:
            with closing(_ro(runtime.ali_path)) as db:
                rows = db.execute('SELECT id,payload,fingerprint,state,created_at FROM inbound LIMIT ?', (MAX_ROWS+1,))
                for index,row in enumerate(rows):
                    if index>=MAX_ROWS:raise ValueError('source_scan_limit')
                    try:
                        x = runtime.ali_record(dict(row))
                    except (ValueError, TypeError, KeyError, OverflowError):
                        continue
                    ident = _key(['alibaba', x['account'], x['scope'], x['conversation_id']])
                    message={'message_id': x['inbound_id'], 'at': _epoch(x['sent_at'], row['created_at']),
                        'direction': x['role'], 'text': x['body'][:65536], 'truncated': len(x['body']) > 65536,
                        'time_basis':'source' if x['sent_at'] else 'received'}
                    _add(by_key,ident,'alibaba',x['account'],x['conversation_id'],x['scope'],message,selected_key,anchor,page_limit,display_name=json.loads(row['payload']).get('buyer_name'),message_search=message_search)
                runtime.history.check_authority(runtime.ali_auth)
            states['alibaba'] = {'status': 'ok'}
        except (ValueError, PermissionError, OSError, sqlite3.Error, KeyError, TypeError):
            by_key = {k:v for k,v in by_key.items() if v['channel'] != 'alibaba'}
    if runtime.wa_path:
        try:
            allowed = set(tuple(x) for x in runtime.wa_pairs)
            with closing(_ro(runtime.wa_path)) as db:
                bindings = db.execute('SELECT session,account_id,owner,scope FROM binding').fetchall()
                if len(bindings)!=1 or (bindings[0]['owner'], bindings[0]['scope']) not in allowed:
                    raise PermissionError('binding_not_authorized')
                b=bindings[0]
                binding_snapshot=tuple(b[k] for k in ('session','account_id','owner','scope'))
                rows=db.execute('SELECT message_key,session,account_id,chat_id,direction,content,sent_at,received,owner,scope '
                    'FROM messages WHERE session=? AND account_id=? AND owner=? AND scope=? LIMIT ?',
                    (b['session'],b['account_id'],b['owner'],b['scope'],MAX_ROWS+1))
                for index,row in enumerate(rows):
                    if index>=MAX_ROWS:raise ValueError('source_scan_limit')
                    stamp=row['sent_at'] if row['sent_at'] is not None else row['received']
                    if not isinstance(row['content'],str) or type(stamp) not in (int,float) or not math.isfinite(stamp):continue
                    ident=_key(['whatsapp',b['session'],b['account_id'],b['owner'],b['scope'],row['chat_id']])
                    message={'message_id':row['message_key'],'at':float(stamp),
                        'direction':row['direction'] if row['direction'] in ('inbound','outbound') else 'unknown',
                        'text':row['content'][:65536],'truncated':len(row['content'])>65536,
                        'time_basis':'source' if row['sent_at'] is not None else 'received'}
                    _add(by_key,ident,'whatsapp',b['account_id'],row['chat_id'],b['scope'],message,selected_key,anchor,page_limit,message_search=message_search)
            states['whatsapp']={'status':'ok'}
        except (ValueError,PermissionError,OSError,sqlite3.Error,KeyError,TypeError):
            by_key={k:v for k,v in by_key.items() if v['channel']!='whatsapp'}
    # Each scope is checked again after all source scans; no response is
    # returned with a scope that was revoked while another source was read.
    if states['wechat']['status'] in ('ok','partial'):
        try:
            with closing(_ro(runtime.wechat_path)) as db:runtime.revalidate(db,groups)
        except (ValueError,PermissionError,OSError,sqlite3.Error,KeyError,TypeError):
            by_key={k:v for k,v in by_key.items() if v['channel']!='wechat'}
            states['wechat']={'status':'unavailable','error_code':'authorization_changed'}
    if states['alibaba']['status']=='ok':
        try:runtime.history.check_authority(runtime.ali_auth)
        except (ValueError,PermissionError,OSError,sqlite3.Error,KeyError,TypeError):
            by_key={k:v for k,v in by_key.items() if v['channel']!='alibaba'}
            states['alibaba']={'status':'unavailable','error_code':'authorization_changed'}
    if states['whatsapp']['status']=='ok':
        try:
            current=type(runtime)(runtime.owner)
            if (current.wa_path != runtime.wa_path or
                set(tuple(x) for x in current.wa_pairs)!=set(tuple(x) for x in runtime.wa_pairs)):
                raise PermissionError('authorization_changed')
            # A grant may remain while its physical account binding is revoked.
            with closing(_ro(current.wa_path)) as db:
                fresh=db.execute('SELECT session,account_id,owner,scope FROM binding').fetchall()
                if len(fresh)!=1 or tuple(fresh[0][k] for k in ('session','account_id','owner','scope'))!=binding_snapshot:
                    raise PermissionError('binding_changed')
        except (ValueError,PermissionError,OSError,sqlite3.Error,KeyError,TypeError):
            by_key={k:v for k,v in by_key.items() if v['channel']!='whatsapp'}
            states['whatsapp']={'status':'unavailable','error_code':'authorization_changed'}
    return by_key, states


def _list_result(by_key, states, anchor, limit, channel=None, search=None):
    items = []
    for item in by_key.values():
        if channel and item['channel'] != channel:
            continue
        if search:
            # Only the metadata of already authorized conversations is searched.
            names = item['_names'] if len(item['_names']) == 1 else ()
            values = (item['account'], item['conversation_id'], *names)
            if not any(search in unicodedata.normalize('NFKC', str(v)).casefold() for v in values):
                continue
        latest = item['latest_at']
        if anchor and not (latest < anchor[0] or (latest == anchor[0] and item['key'] > anchor[1])):
            continue
        items.append({k:item[k] for k in ('key','channel','account','conversation_id','scope')}
                     | {'latest_at': latest, 'latest_time_basis':item['latest_time_basis'], 'message_count': item['message_count'],
                        'display_name': next(iter(item['_names'])) if len(item['_names'])==1 else None})
    items.sort(key=lambda x:(-x['latest_at'],x['key']))
    # Do not advance while an enabled source is unavailable: that could skip it.
    complete = all(states[ch]['status'] in ('ok','partial') for ch in ('wechat','alibaba','whatsapp'))
    page = items[:limit]
    next_cursor = _cursor_encode([page[-1]['latest_at'],page[-1]['key']]) if complete and len(items)>limit and page else None
    return {'items':page, 'channels':states, 'next_cursor':next_cursor, 'pagination_complete':complete}


def _detail_anchor(key, cursor, message_search=None):
    if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('invalid_key')
    anchor = None
    if cursor is not None:
        try:
            if not isinstance(cursor,str) or not 0<len(cursor)<=4096:raise ValueError
            obj=json.loads(base64.b64decode(cursor+'='*(-len(cursor)%4),altchars=b'-_',validate=True))
            if (not isinstance(obj,list) or len(obj)!=(4 if message_search else 3) or obj[2]!=key or (message_search and obj[3]!=hashlib.sha256(message_search.encode()).hexdigest()) or type(obj[0]) not in (int,float)
                    or not math.isfinite(obj[0]) or not isinstance(obj[1],str) or not 0<len(obj[1])<=512):raise ValueError
            anchor=(float(obj[0]),obj[1])
        except (ValueError,TypeError,UnicodeError):raise ValueError('invalid_cursor') from None
    return anchor


def _detail_result(by_key, key, limit, message_search=None):
    item = by_key.get(key)
    if item is None:
        return {'status':'unavailable', 'error_code':'not_found_or_not_authorized', 'items':[]}
    messages = item['messages']
    page=messages[:limit]
    cursor_payload=[page[-1]['at'],page[-1]['message_id'],key] if page else []
    if message_search and page:cursor_payload.append(hashlib.sha256(message_search.encode()).hexdigest())
    next_cursor=_cursor_encode(cursor_payload) if len(messages)>limit and page else None
    return {'status':'ok', 'channel':item['channel'], 'account':item['account'],
            'conversation_id':item['conversation_id'], 'scope':item['scope'],
            'display_name':next(iter(item['_names'])) if len(item['_names'])==1 else None,
            'items':page, 'next_cursor':next_cursor, 'matched_count':item['_message_matches']}


# Follow exactly the existing console's real-time draft admission rule.
# No name-based matching, writes, provider calls or delivery actions.
_DRAFT_UUID = re.compile(r"[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}\Z")
_DRAFT_STATES = frozenset(('draft_queued','draft_processing','draft_waiting','draft_hold','draft_ready','failed'))

def _matching_live_drafts(path, account, conversation, limit=20):
    draft_account = os.environ.get('FOLLOWUP_ICBU_DRAFT_ACCOUNT')
    provenance = os.environ.get('FOLLOWUP_ICBU_PROVENANCE_SCHEMA')
    if not draft_account or not provenance or account != draft_account:
        return {'status':'not_supported', 'items':[], 'has_more':False}
    if not isinstance(conversation,str) or not conversation or len(conversation)>512:
        raise ValueError('invalid_conversation')
    payload = "CASE WHEN json_valid(i.payload) THEN i.payload ELSE '{}' END"
    with closing(_ro(path)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_draft_jobs'").fetchone() is None:
            return {'status':'unavailable','items':[],'has_more':False}
        rows=db.execute("""SELECT i.id,i.state,i.created_at,i.draft,j.state AS job_state
            FROM inbound i JOIN live_draft_jobs j ON j.inbound_id=i.id
            WHERE j.synthetic=0 AND json_extract("""+payload+""",'$.account')=?
            AND json_extract("""+payload+""",'$.conversation_id')=?
            AND json_extract("""+payload+""",'$.history')=0
            AND json_extract("""+payload+""",'$.role')='buyer'
            AND json_extract("""+payload+""",'$.live_provenance.schema')=?
            ORDER BY i.created_at DESC,i.id DESC LIMIT ?""", (account,conversation,provenance,limit+1)).fetchall()
    items=[]
    for row in rows[:limit]:
        if not isinstance(row['id'],str) or not _DRAFT_UUID.fullmatch(row['id']):
            return {'status':'unavailable','items':[],'has_more':False}
        state=row['state'] if row['state'] in _DRAFT_STATES else 'unknown'
        job_state=row['job_state'] if row['job_state'] in _DRAFT_STATES else 'unknown'
        status=state if state==job_state else 'inconsistent'
        body=row['draft'] if status=='draft_ready' and isinstance(row['draft'],str) else ''
        items.append({'id':row['id'],'status':status,'created_at':row['created_at'],
                      'draft':body[:65536], 'truncated':len(body)>65536})
    return {'status':'ok','items':items,'has_more':len(rows)>limit}


def owner_page(authenticated_owner, *, cursor=None, key=None, message_cursor=None, limit=30,
               message_limit=30, authority_factory=NativeAuthority, channel=None, search=None, message_search=None):
    if type(limit) is not int or not 1 <= limit <= 100 or type(message_limit) is not int or not 1 <= message_limit <= 100:
        raise ValueError('invalid_limit')
    if channel not in (None, '', 'wechat', 'alibaba', 'whatsapp'):
        raise ValueError('invalid_channel')
    if search is not None and (not isinstance(search, str) or len(search) > 128 or any(unicodedata.category(c).startswith('C') for c in search)):
        raise ValueError('invalid_search')
    search = unicodedata.normalize('NFKC', search or '').strip().casefold()
    if message_search is not None and (not isinstance(message_search, str) or len(message_search)>128 or any(unicodedata.category(c).startswith('C') for c in message_search)):
        raise ValueError('invalid_message_search')
    message_search=unicodedata.normalize('NFKC',message_search or '').strip().casefold()
    if message_search and key is None:raise ValueError('message_search_requires_conversation')
    list_anchor = _cursor_decode(cursor) if cursor is not None else None
    if key is None and message_cursor is not None:
        raise ValueError('invalid_cursor')
    message_anchor = _detail_anchor(key,message_cursor,message_search) if key is not None else None
    runtime = authority_factory(authenticated_owner)
    by_key, states = _assemble(runtime,selected_key=key,anchor=message_anchor,page_limit=message_limit,message_search=message_search)
    result=_list_result(by_key,states,list_anchor,limit,channel=channel,search=search)
    result['selected']=_detail_result(by_key,key,message_limit,message_search) if key is not None else None
    selected=result['selected']
    if selected and selected.get('status')=='ok' and selected.get('channel')=='alibaba':
        try:
            selected['drafts']=_matching_live_drafts(runtime.ali_path, selected['account'], selected['conversation_id'])
        except (ValueError,PermissionError,OSError,sqlite3.Error,KeyError,TypeError):
            selected['drafts']={'status':'unavailable','items':[],'has_more':False}
        try:
            runtime.history.check_authority(runtime.ali_auth)
        except (ValueError,PermissionError,OSError,sqlite3.Error,KeyError,TypeError):
            # Revocation during the draft read invalidates the original list and detail too.
            result['items']=[item for item in result['items'] if item['channel']!='alibaba']
            result['channels']['alibaba']={'status':'unavailable','error_code':'authorization_changed'}
            result['selected']={'status':'unavailable','error_code':'not_found_or_not_authorized','items':[]}
            result['next_cursor']=None;result['pagination_complete']=False
    return result


def list_conversations(authenticated_owner, *, cursor=None, limit=30, authority_factory=NativeAuthority):
    page=owner_page(authenticated_owner,cursor=cursor,limit=limit,authority_factory=authority_factory)
    page.pop('selected')
    return page


def conversation_detail(authenticated_owner, key, *, limit=30, cursor=None, authority_factory=NativeAuthority):
    return owner_page(authenticated_owner,key=key,message_cursor=cursor,message_limit=limit,
                      authority_factory=authority_factory)['selected']
