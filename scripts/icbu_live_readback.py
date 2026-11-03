#!/usr/bin/env python3
"""Read-only readback of Alibaba inbound rows that claim to be live (history=false).

Prints metadata only (no message text, buyer names or conversation ids): proof validity, role,
timing and duplicate checks, so each natural live event can be verified one by one.
Usage: icbu_live_readback.py [--db PATH] [--since EPOCH] [--json]
"""
import argparse
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime

PROOF_KEYS = {'schema', 'mode', 'epoch_uuid', 'baseline_server_time_utc', 'baseline_watermark_sent_at', 'observed_seq'}


def parse(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or not math.isfinite(stamp.timestamp()):
        raise ValueError
    return stamp.timestamp()


def proof_status(payload, received_at=None):
    proof = payload.get('live_provenance')
    if not isinstance(proof, dict):
        return 'missing'
    if (set(proof) != PROOF_KEYS or proof['schema'] != 'amy-live-v1'
            or proof['mode'] not in ('dom_tail_append', 'native_poll_after_baseline')):
        return 'malformed'
    try:
        if (not isinstance(proof['epoch_uuid'], str) or str(uuid.UUID(proof['epoch_uuid'])) != proof['epoch_uuid']
                or type(proof['observed_seq']) is not int or not 1 <= proof['observed_seq'] <= 1000000):
            return 'malformed'
        baseline, watermark = parse(proof['baseline_server_time_utc']), parse(proof['baseline_watermark_sent_at'])
        sent, observed = parse(payload['sent_at']), parse(payload['observed_at'])
        if not (baseline < sent and watermark < sent <= observed and observed - baseline <= 86400):
            return 'malformed'
        if received_at is not None and not (0 <= received_at - sent <= 300 and observed <= received_at + 30):
            return 'malformed'
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return 'malformed'
    return 'ok'


def safe_metadata(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='/var/lib/imgw/icbu/inbound.sqlite3')
    ap.add_argument('--since', type=float, default=0)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    with closing(sqlite3.connect(Path(args.db).absolute().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        return readback(db, args.since, args.json)


def readback(db, since, as_json):
    db.execute('PRAGMA query_only=ON')
    rows, identities, seqs = [], {}, {}
    for inbound_id, raw, state, reason, created in db.execute(
            'SELECT id,payload,state,archive_reason,created_at FROM inbound WHERE created_at>=? ORDER BY created_at', (since,)):
        payload = json.loads(raw)
        if payload.get('history') is not False:
            continue
        status = proof_status(payload, created)
        try:
            sent, observed = parse(payload['sent_at']), parse(payload['observed_at'])
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError, OSError):
            sent = observed = None
        identity = (payload.get('account'), payload.get('conversation_id'), payload.get('message_id'))
        identities[identity] = identities.get(identity, 0) + 1
        proof = payload.get('live_provenance') if status == 'ok' else {}
        epoch = proof.get('epoch_uuid') if proof else None
        if epoch:
            seqs.setdefault(epoch, []).append(proof['observed_seq'])
        rows.append(dict(
            id=inbound_id[:8] if isinstance(inbound_id, str) and re.fullmatch(r'[a-f0-9-]{36}', inbound_id) else None,
            created=datetime.fromtimestamp(created).strftime('%m-%d %H:%M:%S'),
            role=safe_metadata(payload.get('role'), ('buyer', 'seller', 'system', 'unknown')),
            proof=status, probe_version=payload.get('probe_version') if isinstance(payload.get('probe_version'), str)
            and re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3}){2,5}', payload['probe_version']) else None,
            state=safe_metadata(state, ('archived', 'pending', 'processing', 'retry', 'failed', 'draft_queued', 'draft_ready')),
            archive_reason=safe_metadata(reason, ('history_import', 'role_seller', 'role_system', 'role_unknown',
                'live_drafts_not_enabled', 'role_not_explicit_buyer', 'live_message_id_required',
                'live_probe_version_unverified', 'live_platform_identity_unverified', 'live_proof_missing',
                'live_proof_stale_or_invalid', 'live_epoch_replayed')),
            observed_minus_sent=None if sent is None else round(observed - sent, 1),
            created_minus_observed=None if observed is None else round(created - observed, 1),
            seq=proof.get('observed_seq') if proof else None, message_id_shape_ok=bool(str(payload.get('message_id', '')).replace('.PNM', '').isdigit()),
            ))
    summary = dict(live_rows=len(rows), buyer=sum(r['role'] == 'buyer' for r in rows), seller=sum(r['role'] == 'seller' for r in rows),
                   proof_ok=sum(r['proof'] == 'ok' for r in rows), proof_missing_or_bad=sum(r['proof'] != 'ok' for r in rows),
                   duplicate_identities=sum(1 for n in identities.values() if n > 1),
                   seq_strictly_increasing=(all(s == sorted(set(s)) for s in seqs.values())
                       if seqs and all(r['proof'] == 'ok' for r in rows) else None))
    if as_json:
        print(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False))
    else:
        print(summary)
        for row in rows:
            print(row)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        print('实时来源读回未完成，请核对只读库结构和记录格式；不要据此认定来源有效', file=sys.stderr)
        sys.exit(1)
