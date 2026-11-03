import base64

from console.channels.whatsapp import WhatsAppAdapter
from console.wamedia import MediaStore


def test_webjs_link_preview_and_thumbnail_do_not_fetch_urls(tmp_path):
    adapter = WhatsAppAdapter(media_store=MediaStore(tmp_path / 'db', tmp_path / 'media'),
                              key_path=tmp_path / 'key',
                              fetch=lambda *args: (_ for _ in ()).throw(AssertionError('unexpected fetch')))
    thumb = base64.b64encode(b'\xff\xd8\xfffictional\xff\xd9').decode('ascii')
    message = adapter._message(dict(timestamp=1, body='看看 https://example.test/p?q=1', _data=dict(
        subtype='url', matchedText='https://example.test/p?q=1', canonicalUrl='https://example.test/p?q=1',
        title='虚构标题', description='虚构描述', thumbnail=thumb)))
    block = message['blocks'][-1]
    assert block == dict(type='link', title='虚构标题', desc='虚构描述', url='https://example.test/p?q=1',
                         source='链接预览', thumbnail='data:image/jpeg;base64,' + thumb)
    assert message['blocks'][0]['text'] == '看看 https://example.test/p?q=1'


def test_bad_url_and_thumbnail_do_not_make_unsafe_blocks(tmp_path):
    adapter = WhatsAppAdapter(media_store=MediaStore(tmp_path / 'db', tmp_path / 'media'), key_path=tmp_path / 'key')
    raw = dict(timestamp=1, body='https://example.test/', _data=dict(
        subtype='url', matchedText='https://example.test/', canonicalUrl='javascript:alert(1)',
        title='虚构标题', thumbnail='https://attacker.invalid/image'))
    block = adapter._message(raw)['blocks'][-1]
    assert block['url'] == 'https://example.test/'
    assert 'thumbnail' not in block
    raw['_data']['matchedText'] = 'https://different.invalid/'
    assert 'blocks' not in adapter._message(raw)
    raw['_data']['matchedText'] = 'https://example.test/'
    raw['_data']['thumbnail'] = base64.b64encode(b'\xff\xd8\xff' + b'x' * (64 * 1024) + b'\xff\xd9').decode()
    assert 'thumbnail' not in adapter._message(raw)['blocks'][-1]


def test_buffered_media_has_clear_next_step(tmp_path):
    class Store:
        def get(self, key):
            return dict(state='failed', buffered=True)

    adapter = WhatsAppAdapter(media_store=Store(), key_path=tmp_path / 'key')
    msg = adapter._message(dict(id='fictional', timestamp=1, body='', hasMedia=True), session='fictional')
    assert msg['blocks'][0]['title'] == '附件已保存在本机缓冲，请联系管理员检查存储挂载后重新排队'
