#!/bin/bash
# Operator source/UI rollback only. The operator ledger is retained permanently.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo 'usage: rollback.sh PRIVATE_BACKUP PYTHON' >&2
  exit 2
fi
bundle=$(cd -- "$(dirname -- "$0")" && pwd)
exec "$2" "$bundle/manage.py" rollback --backup "$1" --python "$2"
