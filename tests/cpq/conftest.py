"""Engine-only fixtures."""
import pytest
from console.cpq.models import GateContext


@pytest.fixture
def engine_ctx() -> GateContext:
    return GateContext(images_ok={'MODEL-1': True})


# Application fixtures appended to the original engine fixtures.
from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3
from PIL import Image
from starlette.applications import Starlette
from starlette.testclient import TestClient
from console.cpq.workdb import open_work, ensure_schema
from console.routes.strobe_routes import Settings, build_routes
from console.security.media import MediaAsset, make_asset_uid


@pytest.fixture(scope='session')
def snapshot_db(tmp_path_factory):
    source = Path(os.environ.get('XL_DB_PATH', 'catalog.sqlite3')).expanduser().absolute()
    destination = tmp_path_factory.mktemp('strobe-snapshot') / 'catalog.sqlite3'
    src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    try:
        src.execute('PRAGMA query_only=ON')
        src.row_factory = sqlite3.Row
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return destination


@pytest.fixture
def work_db(tmp_path):
    path = tmp_path / 'cpq_work.sqlite3'
    conn = open_work(path)
    try:
        ensure_schema(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def settings(snapshot_db, work_db, tmp_path):
    return Settings(db_path=snapshot_db, work_db=work_db,
                    media_root=Path(os.environ.get('MEDIA_ROOT', '/var/lib/imgw/media')),
                    media_cache=tmp_path / 'media-cache', testing=True,
                    media_uid_secret='test-secret-not-for-prod')  # Explicit test-only secret.


@pytest.fixture
def app(settings):
    routes = build_routes(settings)
    application = Starlette(routes=routes)
    application.state.strobe_api = routes[0].endpoint.__self__
    application.state.strobe_repository = application.state.strobe_api.repository
    return application


@contextmanager
def _client(application, role=None):
    headers = {} if role is None else {'X-Test-User': f'test-{role}', 'X-Test-Roles': role}
    with TestClient(application, headers=headers) as client:
        yield client


@pytest.fixture
def anon(app):
    with _client(app) as client:
        yield client


@pytest.fixture
def viewer(app):
    with _client(app, 'viewer') as client:
        yield client


@pytest.fixture
def sales(app):
    with _client(app, 'sales') as client:
        yield client


@pytest.fixture
def sales_cost(app):
    with _client(app, 'sales_cost') as client:
        yield client


@pytest.fixture
def manager(app):
    with _client(app, 'manager') as client:
        yield client


@pytest.fixture
def admin(app):
    with _client(app, 'admin') as client:
        yield client


@pytest.fixture
def a_valid_uid(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code == 200, response.text
    images = [image for item in response.json()['items'] for image in item['images']]
    assert images, 'Snapshot has no customer-safe registered strobe images'
    return images[0]['asset_uid']


@pytest.fixture
def planted_symlink_asset(app, tmp_path, monkeypatch):
    root = tmp_path / 'isolated-media'
    root.mkdir()
    external = tmp_path / 'outside.png'
    Image.new('RGB', (8, 8), 'red').save(external)
    link = root / 'escape.png'
    link.symlink_to(external)
    api = app.state.strobe_api
    monkeypatch.setattr(api.media, 'root', root)
    repository = api.repository
    repository._refresh_assets()
    asset_id = 'asset:' + 'f' * 64
    uid = make_asset_uid(repository.media_uid_secret, asset_id)
    monkeypatch.setitem(repository._asset_index, uid, MediaAsset(asset_id, 'equipment:test-symlink', str(link)))
    monkeypatch.setattr(repository, '_refresh_assets', lambda: None)
    return uid
