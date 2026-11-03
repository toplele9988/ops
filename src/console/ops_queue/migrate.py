"""Explicit r11 migration, no source reads, AI, sender or production defaults."""
import argparse
from pathlib import Path
from console.inbox.migrate import migrate as inbox_migrate
from console.archive.store import Store
from console.customers.operations import install


def migrate(archive, outbox, drafts):
    inbox_migrate(archive, outbox, drafts)
    with Store(archive, target_version=9).write() as db:
        install(db)
        db.execute('CREATE TABLE IF NOT EXISTS deal_operation_context ('
                   'deal_id INTEGER PRIMARY KEY REFERENCES deals(id), destination_country TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS deal_operation_receipts ('
                   'scope TEXT NOT NULL, operation_key TEXT NOT NULL, digest TEXT NOT NULL, '
                   'result TEXT NOT NULL, PRIMARY KEY(scope,operation_key))')
        db.execute("CREATE TABLE IF NOT EXISTS deal_draft_outbox (id INTEGER PRIMARY KEY, "
                   "deal_id INTEGER NOT NULL REFERENCES deals(id), quote_id INTEGER, "
                   "kind TEXT NOT NULL, text TEXT NOT NULL, conversation_key TEXT, "
                   "state TEXT NOT NULL CHECK(state='draft'), mode TEXT NOT NULL CHECK(mode='dry-run'), "
                   "created_by TEXT NOT NULL, created_at TEXT NOT NULL)")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='r11 additive migrations; no messages sent')
    for name in ('archive', 'outbox', 'drafts'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    migrate(args.archive, args.outbox, args.drafts)
    print('r11 migration complete')
