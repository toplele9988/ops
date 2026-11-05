"""Merge repeated open "ask owner" items into one per conversation and reason.

Run as the console user. Default is a dry run; --apply backs up the ledger first.
Merged rows become state 'duplicate' (already terminal); nothing is deleted.
"""
import argparse
import json
import sqlite3
import time
from pathlib import Path

OPEN = ('sent', 'done', 'rejected', 'unknown', 'failed', 'duplicate')


def collapse(path, apply=False, backup=None, now=None):
    now = now or time.time()
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        if apply:
            if not backup:
                raise SystemExit('--backup is required with --apply')
            target = sqlite3.connect(backup)
            db.backup(target)
            target.close()
            db.execute('BEGIN IMMEDIATE')
        rows = db.execute("SELECT * FROM operator_actions WHERE kind='ask_owner' AND state NOT IN (%s) ORDER BY id"
                          % ','.join('?' * len(OPEN)), OPEN).fetchall()
        groups = {}
        for row in rows:
            data = json.loads(row['data'])
            groups.setdefault((row['contact_key'], (data.get('payload') or {}).get('reason')), []).append((row, data))
        merged = 0
        for members in groups.values():
            if len(members) < 2:
                continue
            (keep, data), rest = members[0], members[1:]
            data['repeats'] = sum(int(d.get('repeats') or 1) for _, d in members)
            data['last_event_id'] = rest[-1][0]['event_id']
            merged += len(rest)
            if not apply:
                continue
            db.execute('UPDATE operator_actions SET data=?,updated_at=? WHERE id=?', (json.dumps(data, ensure_ascii=False, sort_keys=True), now, keep['id']))
            for row, _ in rest:
                db.execute("UPDATE operator_actions SET state='duplicate',reason='same_open_owner_question',updated_at=? WHERE id=?", (now, row['id']))
                db.execute('INSERT INTO operator_decisions(event_id,action_id,at,phase,data) VALUES(?,?,?,?,?)',
                           (row['event_id'], row['id'], now, 'policy',
                            json.dumps(dict(state='duplicate', reason='same_open_owner_question', merged_into=keep['id']))))
        if apply:
            db.execute('COMMIT')
        return dict(open_before=len(rows), groups=len(groups), merged=merged, open_after=len(rows) - merged, applied=apply)
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default='/var/lib/imgw/ops-app/operator/operator.sqlite3')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    print(json.dumps(collapse(Path(args.database), args.apply, args.backup)))
