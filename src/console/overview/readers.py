"""Only new metadata queries; arrivals and attachment coverage stay in pipeline."""
from console.pipeline.readers import job_counts, require

MAX_MONTHS = 1200


def pause_since(db, now, last):
    require(db, 'heartbeat_log', ('id', 'received_at', 'instance_id', 'state'))
    # Never borrow probe_state.first_seen_at (first ever heartbeat) as pause start.
    # Instance changes, unknown states and >5min gaps all break a continuous run.
    row = db.execute('''WITH valid AS (
        SELECT id,received_at,instance_id,state,
          LAG(received_at) OVER (ORDER BY received_at,id) AS previous_at,
          LAG(instance_id) OVER (ORDER BY received_at,id) AS previous_instance
        FROM heartbeat_log WHERE typeof(received_at) IN ('integer','real')
          AND received_at>0 AND received_at<=:now
    ), marked AS (
        SELECT *,MAX(CASE WHEN state IS NOT 'paused' OR instance_id IS NULL
          OR previous_instance IS NOT instance_id OR received_at-previous_at>300
          THEN id END) OVER (ORDER BY received_at,id ROWS UNBOUNDED PRECEDING) AS boundary
        FROM valid
    ) SELECT MIN(received_at),MAX(received_at),COUNT(*) FROM marked
      WHERE state='paused' AND id>=(SELECT COALESCE(boundary,0) FROM marked
                                    ORDER BY received_at DESC,id DESC LIMIT 1)''', dict(now=now)).fetchone()
    latest = db.execute('''SELECT state FROM heartbeat_log
        WHERE received_at=? ORDER BY id DESC LIMIT 1''', (last,)).fetchone()
    if row[2] and row[1] == last and latest and latest[0] == 'paused':
        return row[0]
    return None


def wechat_knowledge(db, now):
    require(db, 'calls', ('state', 'updated_at'))
    total, processed, last, invalid = db.execute('''SELECT COUNT(*),
        COALESCE(SUM(state='complete'),0),
        MAX(CASE WHEN state='complete' AND typeof(updated_at) IN ('integer','real')
            AND updated_at>0 AND updated_at<=:now THEN updated_at END),
        COALESCE(SUM(CASE WHEN state='complete' AND (typeof(updated_at) NOT IN ('integer','real')
            OR updated_at IS NULL OR updated_at<=0 OR updated_at>:now) THEN 1 ELSE 0 END),0)
        FROM calls''', dict(now=now)).fetchone()
    return dict(total=total, processed=processed, pending=total - processed,
                last_processed_at=last, invalid=invalid)


def alibaba_knowledge(db, now):
    require(db, 'windows', ('version', 'state', 'updated_at'))
    require(db, 'current_conversations', ('analysis_version',))
    # EXISTS avoids multiplying windows if a damaged mapping repeats a version.
    total, processed, last, invalid, blocked, other = db.execute('''SELECT COUNT(*),
        COALESCE(SUM(w.state='validated'),0),
        MAX(CASE WHEN w.state='validated' AND typeof(w.updated_at) IN ('integer','real')
            AND w.updated_at>0 AND w.updated_at<=:now THEN w.updated_at END),
        COALESCE(SUM(CASE WHEN typeof(w.updated_at) NOT IN ('integer','real') OR w.updated_at IS NULL
            OR w.updated_at<=0 OR w.updated_at>:now THEN 1 ELSE 0 END),0),
        COALESCE(SUM(w.state IN ('infra_blocked','needs_repair')),0),
        COALESCE(SUM(w.state IS NULL OR w.state NOT IN
            ('validated','pending','retry_wait','infra_blocked','needs_repair')),0)
        FROM windows w WHERE EXISTS (SELECT 1 FROM current_conversations c
                                    WHERE c.analysis_version=w.version)''', dict(now=now)).fetchone()
    return dict(total=total, processed=processed, pending=total - processed,
                last_processed_at=last, invalid=invalid, blocked=blocked, other=other)


def whatsapp_knowledge(db, now):
    # Reuse the existing fixed-state job counter; do not return unrecognised values.
    result = job_counts(db, 'knowledge_jobs', 'status', 'indexed_claims',
                        ('indexed_claims', 'indexed_no_supported_claims', 'pending', 'raw_indexed',
                         'waiting_model', 'budget_wait', 'model_hold', 'indexed_media_pending'))
    require(db, 'knowledge_jobs', ('updated',))
    last, invalid = db.execute('''SELECT
        MAX(CASE WHEN status IN ('indexed_claims','indexed_no_supported_claims')
            AND typeof(updated) IN ('integer','real') AND updated>0 AND updated<=:now THEN updated END),
        COALESCE(SUM(CASE WHEN typeof(updated) NOT IN ('integer','real') OR updated IS NULL
            OR updated<=0 OR updated>:now THEN 1 ELSE 0 END),0) FROM knowledge_jobs''', dict(now=now)).fetchone()
    states = result['states']
    processed = states['indexed_claims'] + states['indexed_no_supported_claims']
    return dict(total=result['total'], processed=processed, pending=result['total'] - processed,
                last_processed_at=last, invalid=invalid, other=states.get('other', 0),
                blocked=states['model_hold'] + states['budget_wait'])


# JSON values stay inside SQL. No payload, identity, or chat text leaves SQLite.
HISTORY_CTE = '''WITH metadata AS (
    SELECT CASE WHEN json_valid(payload) THEN
        CASE WHEN json_type(payload,'$.sent_at')='text'
            THEN json_extract(payload,'$.sent_at') END END AS sent,
        CASE WHEN json_valid(payload) THEN CASE
            WHEN json_type(payload,'$.account')='text' AND json_type(payload,'$.conversation_id')='text'
            AND length(trim(json_extract(payload,'$.account')))>0
            AND length(trim(json_extract(payload,'$.conversation_id')))>0
            THEN json_array(json_extract(payload,'$.account'),json_extract(payload,'$.conversation_id')) END END AS conv
    FROM inbound
), source AS (
    SELECT conv,CASE WHEN length(sent)<=64 AND
        (substr(sent,-1)='Z' OR (substr(sent,-6,1) IN ('+','-') AND substr(sent,-3,1)=':'))
        THEN CAST(strftime('%s',sent) AS INTEGER) END AS candidate FROM metadata
), valid AS (
    SELECT conv,CASE WHEN candidate>0 AND candidate<=:now THEN candidate END AS at FROM source
) '''


def alibaba_history(db, now):
    require(db, 'inbound', ('payload',))
    total, conversations, first, last, invalid, missing = db.execute(HISTORY_CTE + '''SELECT COUNT(*),
        COUNT(DISTINCT conv),MIN(at),MAX(at),COALESCE(SUM(at IS NULL),0),
        COALESCE(SUM(conv IS NULL),0) FROM valid''', dict(now=now)).fetchone()
    months = db.execute(HISTORY_CTE + '''SELECT strftime('%Y-%m',at,'unixepoch'),COUNT(*)
        FROM valid WHERE at IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT :limit''',
        dict(now=now, limit=MAX_MONTHS + 1)).fetchall()
    if len(months) > MAX_MONTHS:
        raise ValueError('too_many_months')
    return dict(messages=total, conversations=conversations if total and not missing else None,
                earliest_sent_at=first, latest_sent_at=last,
                by_month=[dict(month=month, count=count) for month, count in months],
                invalid=invalid, missing=missing)
