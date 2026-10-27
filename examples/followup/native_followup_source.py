"""Bounded source verification against existing authority; candidate only."""
import json,math,sqlite3
from contextlib import closing
try:
    from . import owner_inbox as inbox
    from .followup_list import require_index,NeedsMore
    from .index_migration import WECHAT,ALIBABA,WHATSAPP
except ImportError:
    import owner_inbox as inbox
    from followup_list import require_index,NeedsMore
    from index_migration import WECHAT,ALIBABA,WHATSAPP

SOURCE_LIMIT=100

class NativeVerifier:
    def __init__(self,owner,factory=inbox.NativeAuthority):
        self.factory=factory;self.runtime=factory(owner);self.owner=owner
        self.groups=None;self.used=set();self.wa_binding=None

    def _groups(self,db):
        if self.groups is None:self.groups=self.runtime.group_grants(db)
        return self.groups

    def resolve(self,note,after=None):
        channel=note['channel'];self.used.add(channel)
        if channel=='wechat':return self._wechat(note,after)
        if channel=='alibaba':return self._alibaba(note,after)
        if channel=='whatsapp':return self._whatsapp(note,after)
        return None

    def _wechat(self,note,after):
        if after is not None and (not isinstance(after,list) or len(after)!=2 or type(after[0]) not in (int,float) or not math.isfinite(after[0]) or not isinstance(after[1],str) or len(after[1])>512):raise ValueError('invalid_source_cursor')
        with closing(inbox._ro(self.runtime.wechat_path)) as db:
            require_index(db,'followup_wechat_source',WECHAT[0])
            matches=[]
            for g in self._groups(db):
                key=inbox._key(['wechat',g['app_id'],g['account'],g['profile'],g['source_scope'],g['conversation']])
                if (key,g['account'],g['source_scope'],g['conversation'])==(note['key'],note['account'],note['scope'],note['conversation_id']):matches.append(g)
            if len(matches)!=1:return None
            g=matches[0]
            sql='''SELECT m.app_id,m.message_id,m.group_id,m.sender_id,m.created_at,m.payload,
                s.own_wxid,s.profile_id,s.scope_id,s.business_role
                FROM messages AS m INDEXED BY followup_wechat_source JOIN message_scopes s ON s.app_id=m.app_id AND s.message_id=m.message_id
                WHERE m.app_id=? AND m.group_id=? AND s.own_wxid=? AND s.profile_id=? AND s.scope_id=?
                AND s.business_role=? AND m.created_at>=?'''
            args=[g['app_id'],g['conversation'],g['account'],g['profile'],g['scope'],g['role'],g['cutoff']]
            if after is not None:sql+=' AND (m.created_at,m.message_id)>(?,?)';args.extend(after)
            sql+=' ORDER BY m.created_at,m.message_id LIMIT ?';args.append(SOURCE_LIMIT+1)
            rows=db.execute(sql,args).fetchall()
            for raw in rows[:SOURCE_LIMIT]:
                r=dict(raw)
                try:
                    payload=json.loads(r['payload']);keys=('app_id','message_id','group_id','sender_id','created_at','own_wxid')
                    if isinstance(payload,dict) and all(payload.get(k)==r[k] for k in keys) and isinstance(payload.get('text'),str) and math.isfinite(float(r['created_at'])):
                        return {'display_name':g.get('display_name')}
                except (ValueError,TypeError,KeyError,OverflowError):continue
            if len(rows)>SOURCE_LIMIT:
                last=rows[SOURCE_LIMIT-1]
                if type(last['created_at']) not in (int,float) or not math.isfinite(last['created_at']) or not isinstance(last['message_id'],str):raise ValueError('invalid_source_cursor')
                raise NeedsMore([last['created_at'],last['message_id']])
            return None

    def _alibaba(self,note,after):
        if after is not None and (not isinstance(after,str) or len(after)>512):raise ValueError('invalid_source_cursor')
        if self.runtime.ali_auth is None or not self.runtime.ali_path:return None
        if inbox._key(['alibaba',note['account'],note['scope'],note['conversation_id']])!=note['key']:return None
        self.runtime.history.check_authority(self.runtime.ali_auth)
        if self.runtime.history.scope_for(self.runtime.ali_auth,note['account'],note['conversation_id'])!=note['scope']:return None
        with closing(inbox._ro(self.runtime.ali_path)) as db:
            require_index(db,'followup_alibaba_source',ALIBABA[0])
            sql="""SELECT id,payload,fingerprint,state,created_at FROM inbound INDEXED BY followup_alibaba_source
                WHERE CASE WHEN json_valid(payload) THEN json_extract(payload, '$.account') END=?
                AND CASE WHEN json_valid(payload) THEN json_extract(payload, '$.conversation_id') END=?
                AND state='archived'"""
            args=[note['account'],note['conversation_id']]
            if after is not None:sql+=' AND id>?';args.append(after)
            sql+=' ORDER BY id LIMIT ?';args.append(SOURCE_LIMIT+1)
            rows=db.execute(sql,args).fetchall()
            names=set();valid=False
            for row in rows[:SOURCE_LIMIT]:
                try:
                    x=self.runtime.ali_record(dict(row))
                    if (x['account'],x['scope'],x['conversation_id'])!=(note['account'],note['scope'],note['conversation_id']):continue
                    valid=True
                    name=inbox._display_name(json.loads(row['payload']).get('buyer_name'))
                    if name:names.add(name)
                except (ValueError,TypeError,KeyError,OverflowError):continue
            # A bounded sample cannot prove a unique display name if more rows
            # may exist. Fall back to the precise conversation ID in that case.
            if valid:return {'display_name':next(iter(names)) if len(rows)<=SOURCE_LIMIT and len(names)==1 else None}
            if len(rows)>SOURCE_LIMIT:
                last=rows[SOURCE_LIMIT-1]['id']
                if not isinstance(last,str):raise ValueError('invalid_source_cursor')
                raise NeedsMore(last)
        return None

    def _whatsapp(self,note,after):
        if after is not None and (not isinstance(after,str) or len(after)>512):raise ValueError('invalid_source_cursor')
        if not self.runtime.wa_path:return None
        with closing(inbox._ro(self.runtime.wa_path)) as db:
            require_index(db,'followup_whatsapp_source',WHATSAPP[0])
            bindings=db.execute('SELECT session,account_id,owner,scope FROM binding').fetchall()
            if len(bindings)!=1:return None
            b=bindings[0];binding=tuple(b[k] for k in ('session','account_id','owner','scope'))
            if (b['owner'],b['scope']) not in set(tuple(x) for x in self.runtime.wa_pairs):return None
            if (b['account_id'],b['scope'])!=(note['account'],note['scope']):return None
            if inbox._key(['whatsapp',b['session'],b['account_id'],b['owner'],b['scope'],note['conversation_id']])!=note['key']:return None
            self.wa_binding=binding
            sql='''SELECT message_key,content,sent_at,received FROM messages INDEXED BY followup_whatsapp_source
                WHERE session=? AND account_id=? AND owner=? AND scope=? AND chat_id=?'''
            args=[*binding,note['conversation_id']]
            if after is not None:sql+=' AND message_key>?';args.append(after)
            sql+=' ORDER BY message_key LIMIT ?';args.append(SOURCE_LIMIT+1)
            rows=db.execute(sql,args).fetchall()
            for row in rows[:SOURCE_LIMIT]:
                stamp=row['sent_at'] if row['sent_at'] is not None else row['received']
                if isinstance(row['content'],str) and type(stamp) in (int,float) and math.isfinite(stamp):return {'display_name':None}
            if len(rows)>SOURCE_LIMIT:
                last=rows[SOURCE_LIMIT-1]['message_key']
                if not isinstance(last,str):raise ValueError('invalid_source_cursor')
                raise NeedsMore(last)
        return None

    def revalidate(self):
        revoked=set()
        if 'wechat' in self.used and self.groups is not None:
            try:
                with closing(inbox._ro(self.runtime.wechat_path)) as db:self.runtime.revalidate(db,self.groups)
            except Exception:revoked.add('wechat')
        if 'alibaba' in self.used:
            try:
                if self.runtime.ali_auth is None:raise ValueError('unavailable')
                self.runtime.history.check_authority(self.runtime.ali_auth)
            except Exception:revoked.add('alibaba')
        if 'whatsapp' in self.used and self.wa_binding is not None:
            try:
                fresh=self.factory(self.owner)
                if fresh.wa_path!=self.runtime.wa_path or set(tuple(x) for x in fresh.wa_pairs)!=set(tuple(x) for x in self.runtime.wa_pairs):raise ValueError('authority_changed')
                with closing(inbox._ro(fresh.wa_path)) as db:
                    rows=db.execute('SELECT session,account_id,owner,scope FROM binding').fetchall()
                if len(rows)!=1 or tuple(rows[0][k] for k in ('session','account_id','owner','scope'))!=self.wa_binding:raise ValueError('binding_changed')
            except Exception:revoked.add('whatsapp')
        return revoked
