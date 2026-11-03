#!/bin/sh
set -eu
exec /usr/bin/python3 -B "$(dirname "$0")/deploy_entities.py" install "$@"
