from console.channels.whatsapp import WhatsAppAdapter
from console.media import tokens
from console.wamedia import MediaStore


def test_projection_returns_signed_attachment_blocks(tmp_path):
    store = MediaStore(tmp_path / 'db', tmp_path / 'media')
    adapter = WhatsAppAdapter(media_store=store, key_path=tmp_path / 'key')
    for kind, mime in [('image', 'image/png'), ('video', 'video/mp4'),
                       ('voice', 'audio/ogg'), ('file', 'application/pdf')]:
        mid = 'fictional-' + kind
        key = store.register('fictional', mid, kind, '虚构资料')
        store.save(key, mime, b'fictional bytes')
        msg = adapter._message({'id': mid, 'timestamp': 1, 'body': '', 'hasMedia': True}, session='fictional')
        block = msg['blocks'][0]
        assert block['type'] == (kind if kind in ('image', 'video') else 'file')
        assert tokens.read(block['media'], tmp_path / 'key') == ['wa', key]
        if kind == 'voice':
            assert '播放器' in block['name']
    key = store.register('fictional', 'unavailable', 'file')
    store.fail(key)
    message = adapter._message({'id': 'unavailable', 'timestamp': 1, 'body': '', 'hasMedia': True}, session='fictional')
    assert message['blocks'][0]['title'] == '附件暂不可取，请联系管理员检查后重新排队'


def test_media_route_authentication_and_attachment_disposition(tmp_path):
    from types import SimpleNamespace
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.testclient import TestClient
    from console import v3_media_api

    store = MediaStore(tmp_path / 'db', tmp_path / 'media')
    key = store.register('fictional', 'document', 'file')
    store.save(key, 'application/pdf', b'%PDF-fictional')
    key_path = tmp_path / 'key'
    token = tokens.make(['wa', key], key_path)
    gate = SimpleNamespace(error_response=lambda request, status: Response(status_code=status))
    app = Starlette(routes=v3_media_api.build(gate, key_path=key_path, wa_store=store))

    async def auth(request, call_next):
        if request.headers.get('X-Fictional-Auth') == 'yes':
            request.state.password_principal = SimpleNamespace(username='fictional')
            request.state.session = SimpleNamespace(authenticated=True)
            request.state.password_recheck = lambda: True
        return await call_next(request)

    from starlette.middleware.base import BaseHTTPMiddleware
    app.add_middleware(BaseHTTPMiddleware, dispatch=auth)
    with TestClient(app) as client:
        assert client.get('/api/v1/media/' + token).status_code == 403
        result = client.get('/api/v1/media/' + token, headers={'X-Fictional-Auth': 'yes'})
        assert result.status_code == 200
        assert result.content == b'%PDF-fictional'
        assert result.headers['content-type'] == 'application/octet-stream'
        assert result.headers['content-disposition'].startswith('attachment;')
        assert client.get('/api/v1/media/' + token + 'bad', headers={'X-Fictional-Auth': 'yes'}).status_code == 404


def test_registration_failure_is_visible_and_keeps_message(tmp_path):
    import sqlite3
    adapter = WhatsAppAdapter(config_path=tmp_path / 'disabled.json')
    class FailedQueue:
        def enqueue(self, *args):
            raise sqlite3.OperationalError('private error')
    adapter.media_ingest = FailedQueue()
    raw = {'id': 'fictional', 'timestamp': 1, 'body': '虚构说明', 'hasMedia': True}
    registered = adapter._register_media(raw, 'fixture', '200002@c.us')
    message = adapter._message(raw, session='fixture')
    assert registered is False
    message = adapter._media_failure(message)
    assert message['text'] == '虚构说明'
    assert message['blocks'][-1]['title'] == '附件登记失败，请联系管理员检查存储后刷新'
    assert 'private error' not in str(message)
