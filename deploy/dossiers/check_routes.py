#!/usr/bin/env python3
"""Build the actual v3 aggregate; prohibit data reads and outgoing connections."""
import argparse
import json
from pathlib import Path
import socket
import sqlite3
import sys
from types import SimpleNamespace


def check(root):
    # A prepared tree contains only changed code. Extend its package search path
    # for unmodified imports; no production code or DB is initialized explicitly.
    sys.path.insert(0, str(root))
    import console
    console.__path__.insert(0, str(root / 'console'))
    def blocked(*args, **kwargs):
        raise AssertionError('route_check_forbids_data_network')
    socket.socket.connect = blocked
    socket.create_connection = blocked
    sqlite3.connect = blocked
    from console import v3
    from starlette.responses import PlainTextResponse
    routes = v3.build(SimpleNamespace(error_response=lambda r,c: PlainTextResponse('error',c)))
    seen = set()
    for r in routes:
        for method in r.methods or ():
            key = (r.path, method)
            if key in seen:
                raise ValueError('duplicate_route')
            seen.add(key)
    for path in ('suppliers', 'suppliers/{id}', 'families', 'families/{id}', 'gaps', 'sources/{id}', 'media/{token}', 'suppliers/{id}/drafts'):
        assert ('/api/v1/dossiers/' + path, 'GET') in seen
    assert ('/api/v1/dossiers/suppliers/{id}/drafts', 'POST') in seen
    assert ('/api/v1/ops/queue', 'GET') in seen
    return dict(passed=True, routes=len(routes), unique_pairs=len(seen), dossier_routes=9)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    result = check(a.root.resolve())
    if a.output:
        a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
