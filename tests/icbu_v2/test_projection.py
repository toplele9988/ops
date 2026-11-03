"""Alibaba projection and authenticated media over fictional bytes and temporary SQLite only."""
import asyncio
from email.message import Message
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from starlette.responses import JSONResponse

from console.channels.alibaba import AlibabaAdapter
from console.channels.alibaba_rich import blocks_for, summary
from console.inbox.model import last_message
from console.media import alibaba, files, tokens
from console import v3_media_api

_spec = importlib.util.spec_from_file_location('projection_attachments',
    Path(__file__).resolve().parents[2] / 'deploy' / 'icbu' / 'icbu_attachments.py')
attachment_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attachment_module)

NOW = 1800000000
INBOUND_ID = '11111111-1111-4111-8111-111111111111'


def message(**extra):
    return dict(account='fictional-account', conversation_id='fictional-conversation', buyer_name='虚构买家',
                role='buyer', content='[图片]', message_id='fictional-platform-message', event_id='fictional-event',
                sent_at='2027-01-15T08:00:00+00:00', attachments=[
                    dict(attachment_id='fictional-image', kind='image', filename='虚构图片.png', mime='image/png', size=16)], **extra)


def seed(path, payload):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE inbound(id TEXT PRIMARY KEY,payload TEXT)')
        db.execute('INSERT INTO inbound VALUES(?,?)', (INBOUND_ID, json.dumps(payload)))


def headers(body, mime):
    result = Message()
    result['Content-Length'] = str(len(body))
    result['Content-Type'] = mime
    result['X-Content-SHA256'] = hashlib.sha256(body).hexdigest()
    return result


def request(token, *, authorised=True, recheck=True, range_header=None):
    return SimpleNamespace(path_params={'token': token}, headers={'range': range_header}, state=SimpleNamespace(
        password_principal=SimpleNamespace(username='虚构用户') if authorised else None,
        session=SimpleNamespace(authenticated=True), password_recheck=Mock(return_value=recheck)))


def test_projection_uses_existing_blocks_opaque_tokens_and_chinese_previews(tmp_path, registry):
    payload = message(cards=[dict(card_type='product', title='虚构样品', url='https://example.invalid/product?ignored=value',
                                subtitle='虚构说明', price_text='虚构价格待核对', moq_text='虚构数量待核对', product_id='fictional-1')])
    source, key_path = tmp_path / 'inbound.sqlite3', tmp_path / 'media.key'
    body = b'\x89PNG\r\n\x1a\nfictional'
    payload['attachments'][0]['size'] = len(body)
    registry.link_message(INBOUND_ID, payload)
    registry.receive_upload(payload['event_id'], 'fictional-image', headers(body, 'image/png'), io.BytesIO(body))
    seed(source, payload)
    adapter = AlibabaAdapter(source, key_path, attachments_db_path=registry.path)
    conversation, = adapter.list_conversations(NOW).values()
    projected, = conversation['messages']
    image, card = projected['blocks']
    assert image['type'] == 'image'
    assert tokens.read(image['media'], key_path) == ['ali', INBOUND_ID, 'fictional-image']
    assert card['type'] == 'link' and card['source'] == '商品卡片'
    assert card['url'] == 'https://example.invalid/product'
    assert '商品编号：fictional-1' in card['desc']
    assert last_message(conversation)['text'].startswith('[图片]')
    assert '_rich' not in projected and '_inbound_id' not in projected
    assert str(tmp_path) not in json.dumps(projected)
    before = source.read_bytes(), source.stat().st_mtime_ns
    AlibabaAdapter(source, key_path, media_tokens=False, attachments_db_path=registry.path).list_conversations(NOW)
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_legacy_payload_keeps_text_and_no_blocks(tmp_path):
    payload = message()
    del payload['attachments']
    payload['content'] = '虚构文字问题'
    source = tmp_path / 'inbound.sqlite3'
    seed(source, payload)
    with patch('console.channels.alibaba.file_stamp', side_effect=AssertionError('legacy must not read media registry')):
        conversation, = AlibabaAdapter(source, tmp_path / 'unused.key').list_conversations(NOW).values()
    assert conversation['messages'][0]['text'] == '虚构文字问题'
    assert 'blocks' not in conversation['messages'][0]
    assert not (tmp_path / 'unused.key').exists()


def test_message_before_upload_refreshes_media_without_reloading_inbound(tmp_path, registry):
    payload = message()
    body = b'\x89PNG\r\n\x1a\nfictional'
    payload['attachments'][0]['size'] = len(body)
    source, key_path = tmp_path / 'inbound.sqlite3', tmp_path / 'media.key'
    seed(source, payload)
    registry.link_message(INBOUND_ID, payload)
    adapter = AlibabaAdapter(source, key_path, attachments_db_path=registry.path)
    before, = adapter.list_conversations(NOW).values()
    assert before['messages'][0]['blocks'][0]['media'] is None
    assert not key_path.exists()
    snapshot, cache_key = adapter._source, adapter.cache_key
    registry.receive_upload(payload['event_id'], 'fictional-image', headers(body, 'image/png'), io.BytesIO(body))
    after, = adapter.list_conversations(NOW + 1).values()
    assert adapter._source is snapshot and adapter.cache_key > cache_key
    token = after['messages'][0]['blocks'][0]['media']
    assert token and key_path.exists()
    assert alibaba.locate(tokens.read(token, key_path), registry.path, registry.root)


def test_unavailable_attachment_registry_preserves_message_metadata(tmp_path):
    source, key_path = tmp_path / 'inbound.sqlite3', tmp_path / 'unused.key'
    seed(source, message())
    adapter = AlibabaAdapter(source, key_path, attachments_db_path=tmp_path / 'missing.sqlite3')
    with patch('console.channels.alibaba.file_stamp', side_effect=PermissionError('fictional unavailable registry')):
        conversation, = adapter.list_conversations(NOW).values()
    assert conversation['messages'][0]['blocks'][0]['media'] is None
    assert adapter.ready and not key_path.exists()


def test_projection_sanitizes_bad_metadata_and_voice_is_downloadable():
    payload = dict(attachments=[dict(attachment_id='voice', kind='voice', filename='虚构语音.mp3', size=9),
                                 dict(attachment_id='../bad', kind='image')],
                   cards=[dict(card_type='inquiry', title='虚构询盘', url='javascript:alert(1)'),
                          dict(card_type='order', title='虚构订单', url='https://user:secret@example.invalid/a'),
                          dict(card_type='rfq', title='虚构采购需求')])
    blocks = blocks_for(payload, '', lambda identity: 'fictional-token')
    assert [block['type'] for block in blocks] == ['file', 'link', 'link', 'link']
    assert blocks[0]['kind'] == 'voice' and blocks[0]['media'] == 'fictional-token'
    assert summary(blocks, '').startswith('[语音]')
    assert all(block.get('url') is None for block in blocks[1:])
    assert blocks_for(dict(cards=[dict(card_type='link', title='\ud800')]), '', lambda _: None)[0]['title'] == '链接'


def test_card_image_reference_is_projected_once_even_without_attachment_metadata():
    card = dict(card_type='product', title='虚构商品', image_attachment_id='fictional-image')
    blocks = blocks_for(dict(cards=[card, card]), '', lambda identity: 'fictional-' + identity)
    assert [block['type'] for block in blocks] == ['image', 'link', 'link']
    assert blocks[0]['media'] == 'fictional-fictional-image'
    payload = message(cards=[card])
    assert [block['type'] for block in blocks_for(payload, '', lambda _: None)] == ['image', 'link']


@pytest.fixture
def registry(tmp_path):
    return attachment_module.AttachmentStore(tmp_path / 'registry' / 'attachments.sqlite3', tmp_path / 'files')


def media_endpoint(tmp_path, registry):
    app = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '请先登录'}, status_code=code))
    return v3_media_api.build(app, db_path=tmp_path / 'missing-wechat.sqlite3', root=tmp_path / 'missing-wechat',
        key_path=tmp_path / 'media.key', alibaba_db_path=registry.path, alibaba_root=registry.root)[0].endpoint


def test_upload_order_does_not_change_media_reference_and_registry_is_readonly(tmp_path, registry):
    body = b'\x89PNG\r\n\x1a\nfictional'
    payload = message()
    payload['attachments'][0]['size'] = len(body)
    token = tokens.make(['ali', INBOUND_ID, 'fictional-image'], tmp_path / 'media.key')
    parts = tokens.read(token, tmp_path / 'media.key')
    registry.receive_upload(payload['event_id'], 'fictional-image', headers(body, 'image/png'), io.BytesIO(body))
    assert alibaba.locate(parts, registry.path, registry.root) is None
    registry.link_message(INBOUND_ID, payload)
    before = Path(registry.path).read_bytes()
    path, size, mime, name = alibaba.locate(parts, registry.path, registry.root)
    assert (size, mime, name) == (len(body), 'image/png', '虚构图片.png')
    assert b''.join(files.iterate(path, 0, size - 1)) == body
    assert Path(registry.path).read_bytes() == before
    result = asyncio.run(media_endpoint(tmp_path, registry)(request(token, range_header='bytes=2-5')))
    assert result.status_code == 206
    assert result.headers['content-range'] == f'bytes 2-5/{len(body)}'
    assert 'content-disposition' not in result.headers
    async def consume():
        return b''.join([part async for part in result.body_iterator])
    assert asyncio.run(consume()) == body[2:6]


@pytest.mark.parametrize('mime,filename,body', [
    ('application/pdf', '虚构资料.pdf', b'%PDF-fictional'),
    ('audio/mpeg', '虚构语音.mp3', b'ID3fictional'),
    ('text/plain', '虚构资料.txt', b'fictional file'),
])
def test_non_image_video_always_download(tmp_path, registry, mime, filename, body):
    payload = message()
    payload['attachments'] = [dict(attachment_id='fictional-file', kind='file', filename=filename, mime=mime, size=len(body))]
    registry.link_message(INBOUND_ID, payload)
    registry.receive_upload(payload['event_id'], 'fictional-file', headers(body, mime), io.BytesIO(body))
    token = tokens.make(['ali', INBOUND_ID, 'fictional-file'], tmp_path / 'media.key')
    result = asyncio.run(media_endpoint(tmp_path, registry)(request(token)))
    assert result.status_code == 200
    assert result.headers['content-disposition'].startswith("attachment; filename*=UTF-8''")
    assert result.headers['x-content-type-options'] == 'nosniff'


def test_auth_tamper_unlinked_traversal_symlink_and_bad_ranges_fail_closed(tmp_path, registry):
    body = b'\x89PNG\r\n\x1a\nfictional'
    payload = message()
    payload['attachments'][0]['size'] = len(body)
    registry.link_message(INBOUND_ID, payload)
    registry.receive_upload(payload['event_id'], 'fictional-image', headers(body, 'image/png'), io.BytesIO(body))
    token = tokens.make(['ali', INBOUND_ID, 'fictional-image'], tmp_path / 'media.key')
    endpoint = media_endpoint(tmp_path, registry)
    for options in (dict(authorised=False), dict(recheck=False)):
        assert asyncio.run(endpoint(request(token, **options))).status_code == 403
    assert asyncio.run(endpoint(request(token + 'x'))).status_code == 404
    for bad_parts in (['ali', INBOUND_ID, '../bad'], ['ali', 'other', 'fictional-image']):
        bad_token = tokens.make(bad_parts, tmp_path / 'media.key')
        assert asyncio.run(endpoint(request(bad_token))).status_code == 404
    assert asyncio.run(endpoint(request(token, range_header='bytes=999-'))).status_code == 416
    located = alibaba.locate(['ali', INBOUND_ID, 'fictional-image'], registry.path, registry.root)
    located[0].unlink()
    located[0].symlink_to(tmp_path / 'outside')
    assert asyncio.run(endpoint(request(token))).status_code == 404


def test_declared_image_with_other_bytes_is_forced_download(tmp_path, registry):
    body = b'<html>fictional</html>'
    payload = message()
    payload['attachments'][0]['size'] = len(body)
    registry.link_message(INBOUND_ID, payload)
    registry.receive_upload(payload['event_id'], 'fictional-image', headers(body, 'image/png'), io.BytesIO(body))
    token = tokens.make(['ali', INBOUND_ID, 'fictional-image'], tmp_path / 'media.key')
    result = asyncio.run(media_endpoint(tmp_path, registry)(request(token)))
    assert result.headers['content-type'] == 'application/octet-stream'
    assert result.headers['content-disposition'].startswith('attachment;')


def test_video_projection_and_inline_range(tmp_path, registry):
    body = b'\x00\x00\x00\x18ftypmp42fictional'
    payload = message()
    payload['attachments'] = [dict(attachment_id='fictional-video', kind='video', filename='虚构视频.mp4',
                                   mime='video/mp4', size=len(body))]
    blocks = blocks_for(payload, '[视频]', lambda _: 'fictional-token')
    assert blocks == [dict(type='video', media='fictional-token', size_bytes=len(body))]
    assert summary(blocks, '') == '[视频]'
    registry.link_message(INBOUND_ID, payload)
    registry.receive_upload(payload['event_id'], 'fictional-video', headers(body, 'video/mp4'), io.BytesIO(body))
    token = tokens.make(['ali', INBOUND_ID, 'fictional-video'], tmp_path / 'media.key')
    result = asyncio.run(media_endpoint(tmp_path, registry)(request(token, range_header='bytes=0-7')))
    assert result.status_code == 206 and result.headers['content-type'] == 'video/mp4'
    assert 'content-disposition' not in result.headers
