#!/bin/bash
# Stop candidate writes first. Preserve all databases and post-install business records.
set -euo pipefail
[ "$#" -eq 1 ] || { echo 'usage: rollback.sh SOURCE_ROOT' >&2; exit 2; }
root=$(realpath "$1")
bundle=$(cd -- "$(dirname -- "$0")" && pwd)
(cd "$root" && sha256sum -c "$bundle/candidate.sha256")
patch --batch --fuzz=0 -R -p1 -d "$root" --dry-run -i "$bundle/production.patch"
patch --batch --fuzz=0 -R -p1 -d "$root" -i "$bundle/production.patch"
(cd "$root" && sha256sum -c "$bundle/baseline.sha256")
while IFS= read -r name; do [ ! -e "$root/$name" ]; done < "$bundle/absent.list"
echo 'backend source restored; databases retained. Restore frontend backup, restart and verify.'
