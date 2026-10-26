"""Standalone candidate runner, disabled unless explicitly configured.

Not installed and not ready for production: local UI is not integrated with
the workbench's authenticated session. No send or revoke operation exists.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from demo_ui import make_server
from encrypted_vault import EncryptedVault
from gmail_oauth import authorize
from gmail_sync import canonical_account
from runtime import GmailBackend, Scheduler, file_key_provider



def read_config(path):
    raw = Path(path).read_bytes()
    if len(raw) > 16384:
        raise ValueError('invalid_config')
    obj = json.loads(raw)
    keys = {'enabled', 'account', 'private_dir', 'key_file', 'poll_interval_seconds', 'ui_port'}
    if not isinstance(obj, dict) or set(obj) != keys or type(obj['enabled']) is not bool:
        raise ValueError('invalid_config')
    obj['account'] = canonical_account(obj['account'])
    expected = os.environ.get('GMAIL_EXPECTED_ACCOUNT')
    if not expected:
        raise ValueError('expected_account_required')
    if obj['account'] != canonical_account(expected):
        raise ValueError('configured_account_mismatch')
    if not Path(obj['private_dir']).is_absolute() or not Path(obj['key_file']).is_absolute():
        raise ValueError('invalid_config')
    if type(obj['poll_interval_seconds']) is not int or obj['poll_interval_seconds'] < 1 or type(obj['ui_port']) is not int or not 1024 <= obj['ui_port'] <= 65535:
        raise ValueError('invalid_config')
    return obj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('serve', 'authorize'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--oauth-port', type=int, default=18792)
    parser.add_argument('--replace-existing', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        cfg = read_config(args.config)
        if not cfg['enabled']:
            raise ValueError('service_disabled')
        folder = Path(cfg['private_dir'])
        provider = file_key_provider(cfg['key_file'])
        if args.action == 'authorize':
            credential = EncryptedVault(folder / 'gmail-credential.enc', provider)
            client = EncryptedVault(folder / 'desktop-client.enc', provider)
            result = authorize(folder, cfg['account'], args.oauth_port, replace_existing=args.replace_existing, vault=credential, client_vault=client)
            print(json.dumps(result), flush=True)
            return
        scheduler = Scheduler(GmailBackend(folder, cfg['account'], cfg['key_file']), cfg['poll_interval_seconds'])
        server = make_server(scheduler, cfg['ui_port'], demo=False)
        scheduler.start()
        try:
            server.serve_forever()
        finally:
            server.server_close()
            scheduler.stop()
    except KeyboardInterrupt:
        pass
    except Exception:
        # Operational diagnostics need a structured safe taxonomy before deploy.
        print('Gmail service failed; inspect local configuration and authorization.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__': main()
