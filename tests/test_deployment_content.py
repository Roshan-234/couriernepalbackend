import pytest
from app.extensions import db
from app.models.site_page import SitePage
from app.models.shipment import Shipment
from app.security import validate_production
from tests.conftest import auth_header, register_and_login


def test_published_content_and_edit_conflict(client, admin_token):
    page = client.get('/api/content/page?path=/about').json
    assert page['heading']
    headers = auth_header(admin_token)
    payload = {key: value for key, value in page.items() if key not in ('path', 'updated_at')}
    payload['heading'] = 'Updated about page'
    saved = client.put('/api/content/admin/page?path=/about', json=payload, headers=headers)
    assert saved.status_code == 200
    assert saved.json['version'] == page['version'] + 1
    assert client.get('/api/content/page?path=/about').json['heading'] == payload['heading']
    assert client.put('/api/content/admin/page?path=/about', json=payload, headers=headers).status_code == 409
    payload['version'] = saved.json['version']
    payload['published'] = False
    assert client.put('/api/content/admin/page?path=/about', json=payload, headers=headers).status_code == 200
    assert client.get('/api/content/page?path=/about').status_code == 404
    payload['version'] += 1
    payload['published'] = True
    client.put('/api/content/admin/page?path=/about', json=payload, headers=headers)


def test_content_role_check_and_immutable_routes(client):
    token = register_and_login(client, 'content-customer@example.test')
    assert client.get('/api/content/admin/pages', headers=auth_header(token)).status_code == 403
    assert client.put('/api/content/admin/page?path=/about', headers=auth_header(token), json={}).status_code == 403


def test_content_seed_preserves_edits(app):
    from app.content.seed import seed_content
    page = SitePage.query.filter_by(path='/about').one()
    before = page.heading
    seed_content()
    assert SitePage.query.filter_by(path='/about').one().heading == before


def test_paginated_shipments_are_scoped_and_bounded(app, client, admin_token):
    token = register_and_login(client, 'pagination@example.test')
    headers = auth_header(token)
    uid = client.get('/api/auth/profile', headers=headers).json['id']
    for index in range(7):
        db.session.add(Shipment(user_id=uid, tracking_no=f'PAGE{index}', status='pending', origin='Test', destination='Destination'))
    db.session.commit()
    first = client.get('/api/shipments/page?per_page=3&page=1', headers=headers).json
    second = client.get('/api/shipments/page?per_page=3&page=2', headers=headers).json
    assert len(first['items']) == 3 and first['pages'] == 3 and first['summary']['total'] == 7
    assert {row['id'] for row in first['items']}.isdisjoint(row['id'] for row in second['items'])
    assert client.get('/api/shipments/page?all=1', headers=headers).status_code == 403
    assert client.get('/api/shipments/page?per_page=10000', headers=headers).status_code == 400
    assert client.get('/api/shipments/page?search=PAGE6', headers=headers).json['total'] == 1
    assert client.get('/api/shipments/page?all=1', headers=auth_header(admin_token)).status_code == 200


def test_backend_key_gate_and_cache_policy(app):
    client = app.test_client()
    previous = app.config['BACKEND_PROXY_SECRET']
    app.config['BACKEND_PROXY_SECRET'] = 'verification-key'
    try:
        assert client.get('/api/health').status_code == 403
        response = client.get('/api/health', headers={'X-Backend-Key': 'verification-key'})
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert client.get('/api/mail-test', headers={'X-Backend-Key': 'verification-key'}).status_code == 404
    finally:
        app.config['BACKEND_PROXY_SECRET'] = previous


def production_config():
    return dict(SECRET_KEY='a'*64, JWT_SECRET_KEY='b'*64, BACKEND_PROXY_SECRET='c'*64,
                SQLALCHEMY_DATABASE_URI='mysql+pymysql://local-test/db', FRONTEND_URL='https://couriernepal.com', RATELIMIT_STORAGE_URI='redis://localhost/0')


@pytest.mark.parametrize('key,value', [('SECRET_KEY', 'change-me'), ('JWT_SECRET_KEY', ''), ('BACKEND_PROXY_SECRET', 'short'),
    ('SQLALCHEMY_DATABASE_URI', 'sqlite:///app.db'), ('FRONTEND_URL', 'http://couriernepal.com'), ('RATELIMIT_STORAGE_URI', 'memory://')])
def test_unsafe_production_settings_fail_fast(key, value):
    config = production_config()
    config[key] = value
    with pytest.raises(RuntimeError):
        validate_production(config)


def test_valid_production_settings():
    validate_production(production_config())
