"""Exercise the real inbox and media routes with archived fictional bytes only."""
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.testclient import TestClient

from console import v3_inbox_api, v3_media_api
from console.channels.common import conversation
from console.channels.whatsapp import WhatsAppAdapter
from console.wamedia import identity
from test_archived import archived, seed


@pytest.mark.parametrize('kind,mime,body', [
    ('image', 'image/png', b'\x89PNG\r\n\x1a\nfictional image'),
    ('video', 'video/mp4', b'\x00\x00\x00\x18ftypmp42fictional video'),
    ('file', 'application/pdf', b'%PDF-fictional document'),
])
def test_archive_to_authenticated_inbox_preview_download_and_range(archived, tmp_path, kind, mime, body):
    bridge, _, store, worker, calls, _ = archived
    seed(archived, body=body, mime=mime)
    worker.scan(bridge)
    assert worker.run_one()
    key_path = tmp_path / 'media.key'
    adapter = WhatsAppAdapter(config_path=tmp_path / 'disabled-config', media_store=store, key_path=key_path)
    message = adapter._message(dict(id='img', timestamp=1, body='', hasMedia=True), session='fixture')
    conv = dict(conversation('whatsapp', 'WhatsApp', '虚构账号', '200002@c.us', '虚构联系人', [message]),
                dormant=False, can_send=False, copy_hint='虚构测试只读取')
    cache = SimpleNamespace(detail=lambda key: conv if key == conv['key'] else None)
    outbox = SimpleNamespace(send_state=lambda _: dict(enabled=False, available=False, reason='虚构测试只读取'))
    gate = SimpleNamespace(error_response=lambda request, code: Response(status_code=code))
    routes = v3_inbox_api.build(gate, tmp_path / 'disabled-inbound', tmp_path / 'drafts.db',
                                inbox=cache, outbox=outbox, hub=SimpleNamespace(), classify_path=tmp_path / 'classify.sqlite3')
    routes += v3_media_api.build(gate, key_path=key_path, wa_store=store)
    app = Starlette(routes=routes)

    async def auth(request, call_next):
        if request.headers.get('X-Fictional-Auth') == 'yes':
            request.state.password_principal = SimpleNamespace(username='fictional')
            request.state.session = SimpleNamespace(authenticated=True)
            request.state.password_recheck = lambda: request.headers.get('X-Fictional-Recheck') != 'deny'
        return await call_next(request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=auth)
    headers = {'X-Fictional-Auth': 'yes'}
    inbox_url = '/api/v1/inbox/conversations/' + conv['key']
    with TestClient(app) as client:
        assert client.get(inbox_url).status_code == 403
        result = client.get(inbox_url, headers=headers)
        assert result.status_code == 200
        block = result.json()['messages'][0]['blocks'][0]
        assert block['type'] == kind and block['size_bytes'] == len(body)
        assert 'thumbnail' not in block
        media_url = '/api/v1/media/' + block['media']
        assert client.get(media_url).status_code == 403
        assert client.get(media_url, headers={**headers, 'X-Fictional-Recheck': 'deny'}).status_code == 403
        result = client.get(media_url, headers=headers)
        assert result.status_code == 200 and result.content == body
        assert result.headers['x-content-type-options'] == 'nosniff'
        if kind == 'file':
            assert result.headers['content-type'] == 'application/octet-stream'
            assert result.headers['content-disposition'].startswith('attachment;')
        else:
            assert result.headers['content-type'] == mime
        part = client.get(media_url, headers={**headers, 'Range': 'bytes=2-7'})
        assert part.status_code == 206 and part.content == body[2:8]
        assert part.headers['content-range'] == f'bytes 2-7/{len(body)}'
        assert client.get(media_url, headers={**headers, 'Range': 'bytes=999999-'}).status_code == 416
        assert client.get(media_url + 'bad', headers=headers).status_code == 404
        digest = store.get(identity('fixture', 'img'))['sha256']
        (store.root / (digest + '.blob')).write_bytes(b'corrupt fictional blob')
        assert client.get(media_url, headers=headers).status_code == 404
    assert calls == []


def test_validation_reason_is_visible_while_waiting_and_when_exhausted(archived, tmp_path):
    bridge, root, store, worker, calls, ticks = archived
    digest = seed(archived)
    (root / digest).unlink()
    worker.scan(bridge)
    adapter = WhatsAppAdapter(config_path=tmp_path / 'disabled-config', media_store=store, key_path=tmp_path / 'media.key')
    raw = dict(id='img', timestamp=1, body='', hasMedia=True)
    for attempt in range(4):
        assert worker.run_one()
        block = adapter._message(raw, session='fixture')['blocks'][0]
        assert ('待重试' if attempt < 3 else '暂不可取') in block['title']
        assert '归档文件缺失' in block['subtitle']
        assert str(tmp_path) not in str(block)
        ticks[0] += 61
    with sqlite3.connect(bridge) as db:
        db.execute("UPDATE media_jobs SET updated=2")
    (root / digest).write_bytes(b'fictional image')
    assert worker.run_one()
    assert adapter._message(raw, session='fixture')['blocks'][0]['type'] == 'image'
    assert calls == []
