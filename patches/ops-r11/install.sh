#!/bin/bash
# Explicit target and private backup location required. No service/network changes.
set -euo pipefail
if [ "$#" -ne 6 ]; then
  echo 'usage: install.sh SOURCE_ROOT PRIVATE_BACKUP ARCHIVE_DB OUTBOX_DB DRAFTS_DB PYTHON' >&2
  exit 2
fi
root=$(realpath "$1")
backup=$2
archive=$3
outbox=$4
drafts=$5
python=$6
bundle=$(cd -- "$(dirname -- "$0")" && pwd)
(cd "$root" && sha256sum -c "$bundle/baseline.sha256")
while IFS= read -r name; do [ ! -e "$root/$name" ] || { echo "new-file collision: $name" >&2; exit 1; }; done < "$bundle/absent.list"
patch --batch --fuzz=0 -p1 -d "$root" --dry-run -i "$bundle/production.patch"
[ ! -e "$backup" ] || { echo 'backup directory already exists' >&2; exit 1; }
mkdir -m 700 -p "$backup"
awk '{print $2}' "$bundle/baseline.sha256" > "$backup/source.list"
tar -C "$root" -czf "$backup/source.tar.gz" -T "$backup/source.list"
"$python" - "$archive" "$outbox" "$drafts" "$backup" <<'PY'
import sqlite3, sys
from pathlib import Path
from contextlib import closing
for label, value in zip(('archive', 'outbox', 'drafts'), sys.argv[1:4]):
    p = Path(value)
    if p.exists():
        with closing(sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(Path(sys.argv[4]) / (label + '.sqlite3'))) as dst:
            src.backup(dst)
PY
patch --batch --fuzz=0 -p1 -d "$root" -i "$bundle/production.patch"
(cd "$root" && sha256sum -c "$bundle/candidate.sha256")
if [ "$(id -u)" = 0 ]; then
  # Verified production service account from both console systemd units.
  runuser -u imgw -- env PYTHONPATH="$root" PYTHONDONTWRITEBYTECODE=1 "$python" -m console.ops_queue.migrate --archive "$archive" --outbox "$outbox" --drafts "$drafts"
else
  PYTHONPATH="$root" PYTHONDONTWRITEBYTECODE=1 "$python" -m console.ops_queue.migrate --archive "$archive" --outbox "$outbox" --drafts "$drafts"
fi
echo 'backend installed and migrated; inspect routes, publish backed-up frontend, restart and verify using the documented runbook'
