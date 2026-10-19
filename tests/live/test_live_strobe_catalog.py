import os
import pytest

pytestmark = pytest.mark.live
EXPECTED = int(os.environ.get('EXPECTED_STROBE_COUNT', '37'))


def _items(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code == 200, response.text
    return response.json()


def test_strobe_count(viewer):
    body = _items(viewer)
    assert body['count'] == len(body['items']) == EXPECTED


def test_every_strobe_has_image_or_is_gated(viewer, app):
    repository = app.state.strobe_repository
    for item in _items(viewer)['items']:
        registered = repository.images_for(item['model_id'])
        safe = [image for image in registered if image['customer_safe']]
        if not safe:
            assert item['images'] == [], item['model_id']
        else:
            assert {image['asset_uid'] for image in item['images']} == {image['asset_uid'] for image in safe}
        assert all(image['customer_safe'] for image in item['images'])


def test_every_strobe_has_cost_basis_or_flagged(viewer, app):
    items = _items(viewer)['items']
    repository = app.state.strobe_repository
    for item in items:
        assert item['has_cost_basis'] is (repository.cost_basis(item['model_id']) is not None), item['model_id']
    missing = [item['model_id'] for item in items if not item['has_cost_basis']]
    assert not missing, f'Models without a valid numeric basis: {missing}'
