"""Explicit additive W2 migration. Paths are required; no production defaults."""
import argparse
from pathlib import Path
from console.archive.store import Store
from console.inbox.workflow import SCHEMA
from console.inbox import drafts
from console.outbox.dry_run import DryRun, SCHEMA as OUTBOX_SCHEMA


def migrate(archive_path, outbox_path, drafts_path):
    with Store(archive_path).write() as db:
        for statement in SCHEMA:
            db.execute(statement)
    with DryRun(outbox_path).store._write() as db:
        db.execute(OUTBOX_SCHEMA)
    drafts.initialize(drafts_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='收件箱 W2 增量建表，不发送消息')
    for name in ('archive', 'outbox', 'drafts'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    migrate(args.archive, args.outbox, args.drafts)
    print('W2 migration complete; no drafts generated or messages sent')
