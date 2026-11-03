#!/usr/bin/env python3
"""Explicit migration/replay; opens only the configured state, never a provider."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading


class State:
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def close(self):
        self.db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('migrate', 'replay'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error('limit must be 1..1000')
    # Do not call the production config loader: it can chmod the file.
    config = json.loads(args.config.read_text())
    state_path = Path(config['state_file'])
    if not state_path.is_absolute():
        state_path = args.config.parent / state_path
    store = State(state_path)
    try:
        import wechat_private
        for table, required in {'messages': {'app_id','message_id','payload','status','group_id'},
                                'message_scopes': {'profile_id','scope_id','own_wxid','business_role'}}.items():
            found = {r[1] for r in store.db.execute('PRAGMA table_info(' + table + ')')}
            if not required <= found:
                raise ValueError('private_baseline_schema_missing')
        wechat_private.initialize(store)
        result = {'migrated': 1} if args.action == 'migrate' else wechat_private.replay(store, config, limit=args.limit)
        print(json.dumps(result, sort_keys=True))
    finally:
        store.close()


if __name__ == '__main__':
    main()
