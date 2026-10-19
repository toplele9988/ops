from io import BytesIO
import hashlib
import hmac
import json
import os
import re
from PIL import Image
import pytest
from console.security.media import MediaAsset, MediaError, MediaService, make_asset_uid


def test_all_strobe_images_resolve(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code == 200, response.text
    items = response.json()['items']
    for it in items:
        for img in it['images']:
            for url in (img['thumb_url'], img['large_url'], img['orig_url']):
                r = viewer.get(url)
                assert r.status_code == 200, url
                assert r.headers['content-type'].startswith('image/')
                assert r.headers['x-content-type-options'] == 'nosniff'
                assert r.content[:4] in (b'\x89PNG', b'RIFF') or r.content[:3] == b'\xff\xd8\xff'
                assert r.headers['cache-control'] == 'private, max-age=86400'
                assert r.headers['etag']


def test_media_requires_auth(anon, a_valid_uid):
    assert anon.get(f'/media/a/{a_valid_uid}').status_code == 401


@pytest.mark.parametrize('bad', ['../etc/passwd', '..%2F..%2Fetc%2Fpasswd', 'a_%00', 'a_zzzz', '/abs/path', 'a_..\\x'])
def test_media_traversal_rejected(viewer, bad):
    assert viewer.get(f'/media/a/{bad}').status_code in (400, 404)


def test_media_bad_width(viewer, a_valid_uid):
    response = viewer.get(f'/media/a/{a_valid_uid}?w=999')
    assert response.status_code == 400
    assert response.json() == {'error': {'code': 'BAD_WIDTH'}}


def test_media_symlink_escape(viewer, planted_symlink_asset):
    assert viewer.get(f'/media/a/{planted_symlink_asset}').status_code == 404


def test_uid_not_guessable():
    secret = 'test-secret-not-for-prod'
    asset_ids = [f'asset:{number:064x}' for number in range(10, 18)]
    uids = [make_asset_uid(secret, asset_id) for asset_id in asset_ids]
    assert len(set(uids)) == len(uids)
    for asset_id, uid in zip(asset_ids, uids):
        assert re.fullmatch(r'a_[0-9a-f]{16}', uid)
        assert asset_id not in uid
        assert uid == 'a_' + hmac.new(secret.encode(), asset_id.encode(), hashlib.sha256).hexdigest()[:16]
        assert uid != make_asset_uid('different-test-secret-not-for-prod', asset_id)
    for first, second in zip(uids, uids[1:]):
        assert (int(first[2:], 16) ^ int(second[2:], 16)).bit_count() >= 16


def test_legacy_media_route_unchanged(viewer, app):
    # This isolated app has no legacy route. Invalid UID JSON identifies the
    # new handler; integration must put it before the generic legacy pattern.
    response = viewer.get('/media/a/x')
    assert response.status_code == 404
    assert response.json() == {'error': {'code': 'NOT_FOUND'}}
    assert viewer.get('/media/fengwu/FW-X.png').status_code == 404
    assert all(route.path != '/media/{folder}/{name}' for route in app.routes)


def test_unregistered_uid_is_not_a_filesystem_lookup(viewer, app):
    repository = app.state.strobe_repository
    uid = make_asset_uid(repository.media_uid_secret, 'asset:unregistered-test-only')
    assert repository.asset_by_uid(uid) is None
    assert viewer.get(f'/media/a/{uid}').status_code == 404


def test_etag_auth_precedes_conditional_response(viewer, anon, a_valid_uid):
    url = f'/media/a/{a_valid_uid}?w=320'
    response = viewer.get(url)
    assert response.status_code == 200
    headers = {'If-None-Match': response.headers['etag']}
    cached = viewer.get(url, headers=headers)
    assert cached.status_code == 304
    assert cached.headers['x-content-type-options'] == 'nosniff'
    assert anon.get(url, headers=headers).status_code == 401


def test_thumbnail_rebuilds_when_source_mtime_changes(tmp_path):
    root = tmp_path / 'media'
    root.mkdir()
    source = root / 'source.png'
    Image.new('RGB', (500, 100), 'red').save(source)
    uid = make_asset_uid('test-secret-not-for-prod', 'asset:thumbnail-test')
    asset = MediaAsset('asset:thumbnail-test', 'equipment:test', str(source))
    service = MediaService(root, tmp_path / 'cache')
    first = service.render(uid, asset, 320)
    metadata = tmp_path / 'cache' / 'thumbs' / f'{uid}_320.json'
    old_token = json.loads(metadata.read_text())['source']
    old_mtime = source.stat().st_mtime_ns
    Image.new('RGB', (500, 100), 'blue').save(source)
    os.utime(source, ns=(old_mtime + 2_000_000_000, old_mtime + 2_000_000_000))
    second = service.render(uid, asset, 320)
    assert first.body != second.body
    assert first.etag != second.etag
    assert json.loads(metadata.read_text())['source'] != old_token
    with Image.open(BytesIO(second.body)) as image:
        assert image.format == 'WEBP' and image.width == 320
    assert service.render(uid, asset, 320).body == second.body


def test_magic_must_match_extension(tmp_path):
    root = tmp_path / 'media'
    root.mkdir()
    source = root / 'false.png'
    Image.new('RGB', (8, 8), 'red').save(source, format='JPEG')
    uid = make_asset_uid('test-secret-not-for-prod', 'asset:bad-magic')
    service = MediaService(root, tmp_path / 'cache')
    with pytest.raises(MediaError) as error:
        service.render(uid, MediaAsset('asset:bad-magic', None, str(source)), None)
    assert error.value.status_code == 404
