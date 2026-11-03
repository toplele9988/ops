#!/usr/bin/env python3
"""Plan/import an audited dossier into NEW sd_* tables; never mutate legacy rows.

Dry run is the default. Conflicting immutable primary keys abort the transaction.
This is a table import utility, not a worker or production deployment command.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from prepare import readonly, qi

TABLES = ['metadata','source','supplier','product','supplier_product','offer',
          'evidence','gap','legacy_map','merge_event']


def target_sql():
    sql = (Path(__file__).parent / 'schema.sql').read_text()
    # Only identifier tokens are replaced. No business strings enter this DDL.
    sql = re.sub(r'\b(' + '|'.join(TABLES) + r')\b', lambda m: 'sd_' + m[0], sql)
    return re.sub(r'CREATE INDEX (\w+)', r'CREATE INDEX sd_\1', sql)


def columns(db, table):
    return list(db.execute('PRAGMA table_info(' + qi(table) + ')'))


def import_candidate(candidate, target, apply=False):
    candidate, target = Path(candidate).resolve(strict=True), Path(target).resolve()
    if candidate == target:
        raise ValueError('candidate_and_target_must_differ')
    with readonly(candidate) as src:
        rev = src.execute("SELECT value FROM metadata WHERE key='schema_revision'").fetchone()
        if not rev or rev[0] != 'dossier-v1':
            raise ValueError('unsupported_candidate_revision')
        if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or list(src.execute('PRAGMA foreign_key_check')):
            raise ValueError('invalid_candidate')
        counts = {t: src.execute('SELECT count(*) FROM ' + qi(t)).fetchone()[0] for t in TABLES}
        schema = {t: [tuple(r) for r in columns(src, t)] for t in TABLES}
        if target.exists():
            with readonly(target) as dst:
                existing = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                present = {'sd_' + t for t in TABLES} & existing
                if present and present != {'sd_' + t for t in TABLES}:
                    raise ValueError('partial_sd_schema_requires_explicit_migration')
                for t in TABLES:
                    if 'sd_' + t in existing and [tuple(r) for r in columns(dst, 'sd_' + t)] != schema[t]:
                        raise ValueError('incompatible_target_schema:' + t)
        else:
            existing = set()
        plan = {'apply': apply, 'candidate_schema': rev[0], 'counts': counts,
                'target_namespace': 'sd_*', 'legacy_tables_changed': False}
        if not apply:
            return plan
        if not target.parent.is_dir():
            raise ValueError('target_parent_must_exist')
        # The explicit output target is the only write connection in this tool.
        dst = sqlite3.connect(target, isolation_level=None)
        try:
            dst.execute('PRAGMA foreign_keys=ON')
            dst.execute('BEGIN IMMEDIATE')
            # Avoid executescript's implicit COMMIT; schema + rows are atomic.
            if not any('sd_' + t in existing for t in TABLES):
                for statement in target_sql().split(';'):
                    if statement.strip():
                        dst.execute(statement)
            inserted = {}
            for table in TABLES:
                info = schema[table]
                names = [r[1] for r in info]
                pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
                where = ' AND '.join(qi(k) + '=?' for k in pk)
                inserted[table] = 0
                for r in src.execute('SELECT * FROM ' + qi(table)):
                    vals = tuple(r)
                    prior = dst.execute('SELECT * FROM ' + qi('sd_' + table) + ' WHERE ' + where,
                                        [r[k] for k in pk]).fetchone()
                    if prior is not None:
                        if table == 'metadata' and r['key'] in {'built_at', 'reviewer'}:
                            # Preserve the target import's original metadata.
                            continue
                        if tuple(prior) != vals:
                            raise ValueError('immutable_key_conflict:' + table)
                        continue
                    dst.execute('INSERT INTO ' + qi('sd_' + table) + ' (' + ','.join(map(qi, names)) +
                                ') VALUES (' + ','.join('?' for _ in names) + ')', vals)
                    inserted[table] += 1
            if list(dst.execute('PRAGMA foreign_key_check')):
                raise ValueError('target_foreign_key_failure')
            dst.execute('COMMIT')
            plan['inserted'] = inserted
        except BaseException:
            dst.execute('ROLLBACK')
            raise
        finally:
            dst.close()
        return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--candidate', type=Path, required=True)
    ap.add_argument('--target', type=Path, required=True)
    ap.add_argument('--apply', action='store_true', help='Write the specified output target. Default is read-only plan.')
    a = ap.parse_args()
    print(json.dumps(import_candidate(a.candidate, a.target, a.apply), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
