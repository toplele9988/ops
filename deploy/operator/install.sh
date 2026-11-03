#!/bin/bash
# Explicit invocation only. No systemctl, provider clients or network changes.
set -euo pipefail
if [ "$#" -ne 6 ]; then
  echo 'usage: install.sh BACKEND_ROOT WEB_ROOT PRIVATE_BACKUP OPERATOR_DB DIST_DIR PYTHON' >&2
  exit 2
fi
bundle=$(cd -- "$(dirname -- "$0")" && pwd)
exec "$6" "$bundle/manage.py" install --source-root "$1" --web-root "$2" --backup "$3" --database "$4" --dist "$5" --python "$6"
