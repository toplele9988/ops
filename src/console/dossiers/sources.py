"""Resolve only registered supplier sources; never accept a client file path.

The existing console/media token signer is reused. Custom sd tokens carry a
source identity and expected content hash, never an absolute filesystem path.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode

from ..inbox.model import make_key
from ..media import files, tokens
from .store import ROOT, readonly


class Sources:
    def __init__(self, *, config_path=ROOT / 'runtime.json', config=None):
        self.config_path, self.config = config_path, config

    def settings(self):
        if self.config is not None:
            return self.config
        return json.loads(Path(self.config_path).read_text())

    @staticmethod
    def is_image(source):
        coverage = json.loads(source['coverage_json'])
        return (source['kind'] == 'nas_file' and Path(source['external_key']).suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.gif')) or (
            source['kind'] in ('message_attachment', 'note_asset') and (coverage.get('format') or '').lower() in ('jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'))

    def message(self, source):
        if source['scope'] != 'supplier':
            raise ValueError('source_scope_unavailable')
        settings = self.settings()
        parts = json.loads(source['external_key'])
        with readonly(settings['wechat_db']) as db:
            row = db.execute('SELECT m.group_id,m.created_at,m.payload FROM messages m WHERE m.app_id=? AND m.message_id=? '
                "AND EXISTS(SELECT 1 FROM message_scopes s WHERE s.app_id=m.app_id AND s.message_id=m.message_id AND s.business_role='supplier')", parts[:2]).fetchone()
            if not row:
                raise ValueError('source_scope_unavailable')
            if source['kind'] == 'message' and hashlib.sha256(row['payload'].encode()).hexdigest() != source['content_sha256']:
                raise ValueError('source_changed')
            account = '微信'
            if not row['group_id'].endswith('@chatroom'):
                # Private-chat accounts are scoped by app AND self identity in
                # the reviewed companion adapter; never reuse the group key.
                from ..channels.wechat_private import account_key
                private = db.execute('SELECT own_wxid,peer_id FROM wechat_private_messages WHERE app_id=? AND message_id=?', parts[:2]).fetchone()
                if not private or private['peer_id'] != row['group_id']:
                    raise ValueError('private_binding_unavailable')
                account = account_key(parts[0], private['own_wxid'])
            return dict(conversation_id=row['group_id'], message_id=parts[1], message_at=row['created_at'],
                        conversation_url='/app/inbox?' + urlencode(dict(conversation=make_key('conversation', 'wechat', account, row['group_id']), channel='wechat', message=parts[1])),
                        original_text=json.loads(row['payload']).get('text', '') if source['kind'] == 'message' else None)

    def locate(self, source):
        """Return a confined, hash-bound file. No network or recovery writes."""
        if source['scope'] != 'supplier':
            raise ValueError('source_scope_unavailable')
        settings = self.settings()
        expected = source['file_sha256']
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('original_unavailable')
        if source['kind'] == 'nas_file':
            root = Path(settings['nas_root']).absolute()
            relative = Path(source['external_key'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('unsafe_reference')
            path = root / relative
            # _open below rejects symlinks in every component as well.
            if not path.is_relative_to(root):
                raise ValueError('unsafe_reference')
        elif source['kind'] in ('message_attachment', 'note_asset'):
            self.message(source)  # current supplier scope is required even for an old token
            parts = json.loads(source['external_key'])
            if len(parts) != 4:
                raise ValueError('unsafe_reference')
            with readonly(settings['wechat_db']) as db:
                if source['kind'] == 'note_asset':
                    row = db.execute("SELECT archive_sha256 FROM company_note_assets WHERE app_id=? AND message_id=? AND parent_ordinal=? AND ordinal=? AND archive_status='ready'", parts).fetchone()
                else:
                    row = db.execute("SELECT archive_sha256 FROM company_media WHERE app_id=? AND message_id=? AND ordinal=? AND archive_status='ready'", (parts[0], parts[1], parts[3])).fetchone()
            if not row or row[0] != expected:
                raise ValueError('source_changed')
            path = Path(settings['blob_root']) / (expected + '.blob')
        else:
            raise ValueError('original_unavailable')
        with files._open(path) as stream:
            data = stream.read(files.MAX_BYTES + 1)
        if not data or len(data) > files.MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('source_changed_or_large')
        return data, files.sniff(data[:16])

    def resolve(self, source):
        coverage = json.loads(source['coverage_json'])
        result = dict(filename=Path(source['external_key']).name if source['kind'] == 'nas_file' else coverage.get('name'),
                      media_url=None, mime=None, available=False)
        try:
            if source['kind'] in ('message', 'message_attachment', 'note_asset'):
                result.update(self.message(source))
            if source['kind'] == 'message':
                result['available'] = True
            elif source['kind'] in ('nas_file', 'message_attachment', 'note_asset'):
                _, mime = self.locate(source)
                key_path = Path(self.settings()['media_key'])
                # tokens._key can create a key: never invoke it for an absent file.
                if not key_path.is_file() or key_path.is_symlink():
                    raise ValueError('media_key_unavailable')
                token = tokens.make(['sd', source['source_id'], source['file_sha256']], key_path)
                result.update(available=True, media_url='/api/v1/dossiers/media/' + token, mime=mime)
        except (OSError, ValueError, TypeError, KeyError, ImportError, sqlite3.Error):
            result['availability_note'] = '原件不可用、内容已变更或授权范围待核对；下方保留归档证据。'
        return result

    def media(self, token, service):
        key_path = Path(self.settings()['media_key'])
        if not key_path.is_file() or key_path.is_symlink():
            raise ValueError('media_key_unavailable')
        parts = tokens.read(token, key_path)
        if not parts or len(parts) != 3 or parts[0] != 'sd':
            raise ValueError('invalid_media_token')
        source = service.source_row(parts[1])
        if parts[2] != source['file_sha256']:
            raise ValueError('source_changed')
        return self.locate(source)
