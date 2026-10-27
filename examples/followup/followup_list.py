"""Bounded shared follow-up listing. No write or external send on requests."""
import base64,json,math,re,sqlite3
from urllib.parse import urlencode
try:from . import conversation_notes as notes
except ImportError:import conversation_notes as notes

MAX_SCAN=200
STATUS_SQL="CREATE INDEX followup_status_order ON conversation_notes(status, updated_at DESC, key DESC)"
ALL_SQL="CREATE INDEX followup_all_order ON conversation_notes(updated_at DESC, key DESC)"

class MissingIndex(Exception):pass
class NeedsMore(Exception):
    def __init__(self,after):self.after=after

def require_index(db,name,sql):
    row=db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",(name,)).fetchone()
    if not row or ' '.join((row[0] or '').split())!=' '.join(sql.split()):raise MissingIndex('followup_index_missing')

def encode_cursor(status,stamp,key):
    return base64.urlsafe_b64encode(json.dumps([status,stamp,key],separators=(',',':')).encode()).decode().rstrip('=')

def decode_cursor(value,status):
    try:
        if not isinstance(value,str) or not 0<len(value)<=512:raise ValueError()
        x=json.loads(base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True))
        if not isinstance(x,list) or len(x)!=3 or x[0]!=status or type(x[1]) not in (int,float) or not math.isfinite(x[1]) or not isinstance(x[2],str) or not re.fullmatch('[0-9a-f]{64}',x[2]):raise ValueError()
        return float(x[1]),x[2]
    except (ValueError,TypeError,UnicodeError):raise ValueError('invalid_cursor') from None

def source_token(status,row,after):
    payload=[status,row['key'],row['channel'],after]
    return base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')

def decode_source_token(token,status):
    try:
        if not isinstance(token,str) or not 0<len(token)<=2048:raise ValueError()
        value=json.loads(base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True))
        if (not isinstance(value,list) or len(value)!=4 or value[0]!=status or
            not isinstance(value[1],str) or not re.fullmatch('[0-9a-f]{64}',value[1]) or
            value[2] not in ('wechat','alibaba','whatsapp')):raise ValueError()
        return value[1],value[2],value[3]
    except (ValueError,TypeError,UnicodeError):raise ValueError('invalid_source_cursor') from None

def list_page(verifier,status='pending',cursor=None,limit=30,*,path=notes.DB):
    """Verifier rechecks source/grants and returns metadata, not a name-based match."""
    if status not in notes.STATES and status!='all':raise ValueError('invalid_status')
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('invalid_limit')
    anchor=decode_cursor(cursor,status) if cursor else None
    where=[];args=[]
    if status!='all':where.append('status=?');args.append(status)
    if anchor:where.append('(updated_at,key)<(?,?)');args.extend(anchor)
    index='followup_status_order' if status!='all' else 'followup_all_order'
    sql='SELECT key,channel,account,scope,conversation_id,status,content,version,updated_at,updated_by FROM conversation_notes INDEXED BY '+index
    if where:sql+=' WHERE '+' AND '.join(where)
    sql+=' ORDER BY updated_at DESC,key DESC LIMIT ?';args.append(MAX_SCAN+1)
    with notes._conn(path) as db:
        require_index(db,'followup_status_order' if status!='all' else 'followup_all_order',STATUS_SQL if status!='all' else ALL_SQL)
        rows=[dict(row) for row in db.execute(sql,args)]
    candidate_count=len(rows)
    selected=[];unavailable=False;last=None;processed=0;pending=[]
    for row in rows[:MAX_SCAN]:
        if len(selected)>=limit:break
        processed+=1
        if (type(row['updated_at']) not in (int,float) or not math.isfinite(row['updated_at']) or not isinstance(row['key'],str) or not re.fullmatch('[0-9a-f]{64}',row['key'])):
            raise ValueError('invalid_note_order')
        last=(row['updated_at'],row['key'])
        try:
            notes._scope(row['key'],row['channel'],row['account'],row['scope'],row['conversation_id'])
            if (row['status'] not in notes.STATES or type(row['version']) is not int or row['version']<1 or
                not isinstance(row['content'],str) or len(row['content'])>512 or
                not isinstance(row['updated_by'],str) or not notes.ACTOR.fullmatch(row['updated_by'])):
                raise ValueError('invalid_note')
            # Note content is exposed only when the exact identity remains visible.
            metadata=verifier.resolve(row)
        except NeedsMore as exc:
            pending.append({'channel':row['channel'],'token':source_token(status,row,exc.after)})
            unavailable=True
            break
        except Exception:
            metadata=None;unavailable=True
        if metadata is None:
            unavailable=True;continue
        if not isinstance(metadata,dict):raise ValueError('invalid_source_metadata')
        item={k:row[k] for k in ('key','channel','account','scope','conversation_id','status','content','version','updated_at','updated_by')}
        item['display_name']=metadata.get('display_name')
        item['_source_after']=None
        item['detail_url']='/v3/workbench?'+urlencode({'conversation':row['key']})
        selected.append(item)
    # Revocation while listing invalidates matching channel items, not just future pages.
    revoked_all=set()
    def check_authority(items):
        nonlocal unavailable
        revoked=verifier.revalidate()
        if not isinstance(revoked,set) or not revoked.issubset({'wechat','alibaba','whatsapp'}):raise ValueError('invalid_authority_result')
        if revoked:
            revoked_all.update(revoked)
            unavailable=True
            return [x for x in items if x['channel'] not in revoked]
        return items
    selected=check_authority(selected)
    # A grant can remain valid after its source message disappears. Re-read the
    # exact source before rendering; this is still not a cross-database atomic
    # snapshot, so a later deletion is possible and must be checked on entry.
    current=[]
    for item in selected:
        try:metadata=verifier.resolve(item,item.get('_source_after'))
        except Exception:metadata=None
        if not isinstance(metadata,dict):unavailable=True;continue
        item['display_name']=metadata.get('display_name');current.append(item)
    selected=check_authority(current)
    # Same-DB version/status check prevents returning a known-stale follow-up.
    if selected:
        with notes._conn(path) as db:
            marks=','.join('?' for _ in selected)
            rows=db.execute('SELECT key,version,status FROM conversation_notes WHERE key IN ('+marks+')',[x['key'] for x in selected]).fetchall()
        latest={r['key']:(r['version'],r['status']) for r in rows}
        current=[]
        for item in selected:
            if latest.get(item['key'])!=(item['version'],item['status']):unavailable=True;continue
            current.append(item)
        selected=current
    more=processed<candidate_count
    next_cursor=encode_cursor(status,*last) if more and last else None
    pending=[p for p in pending if p['channel'] not in revoked_all]
    for item in selected:item.pop('_source_after',None)
    return {'items':selected,'status_filter':status,'next_cursor':next_cursor,'partial':unavailable,'scanned':processed,'pending':pending}

def verify_one(verifier,status,token,*,path=notes.DB):
    """Resume one authorized source using a bounded, keyset-based cursor."""
    if status not in notes.STATES and status!='all':raise ValueError('invalid_status')
    key,channel,after=decode_source_token(token,status)
    with notes._conn(path) as db:
        raw=db.execute('SELECT key,channel,account,scope,conversation_id,status,content,version,updated_at,updated_by FROM conversation_notes WHERE key=?',(key,)).fetchone()
    empty={'items':[],'status_filter':status,'next_cursor':None,'partial':True,'scanned':1,'pending':[],'verifying':True}
    if raw is None:return empty
    row=dict(raw)
    if row['channel']!=channel or (status!='all' and row['status']!=status):return empty
    try:
        notes._scope(row['key'],row['channel'],row['account'],row['scope'],row['conversation_id'])
        if (row['status'] not in notes.STATES or type(row['version']) is not int or row['version']<1 or
            not isinstance(row['content'],str) or len(row['content'])>512 or
            not isinstance(row['updated_by'],str) or not notes.ACTOR.fullmatch(row['updated_by'])):return empty
        metadata=verifier.resolve(row,after)
    except NeedsMore as exc:
        if channel not in verifier.revalidate():empty['pending']=[{'channel':channel,'token':source_token(status,row,exc.after)}]
        return empty
    except Exception:return empty
    if not isinstance(metadata,dict) or channel in verifier.revalidate():return empty
    try:again=verifier.resolve(row,after)
    except NeedsMore as exc:
        if channel not in verifier.revalidate():empty['pending']=[{'channel':channel,'token':source_token(status,row,exc.after)}]
        return empty
    except Exception:return empty
    if not isinstance(again,dict) or channel in verifier.revalidate():return empty
    with notes._conn(path) as db:
        latest=db.execute('SELECT version,status FROM conversation_notes WHERE key=?',(key,)).fetchone()
    if latest is None or (latest['version'],latest['status'])!=(row['version'],row['status']):return empty
    row['display_name']=again.get('display_name');row['detail_url']='/v3/workbench?'+urlencode({'conversation':key})
    return {'items':[row],'status_filter':status,'next_cursor':None,'partial':False,'scanned':1,'pending':[],'verifying':True}
