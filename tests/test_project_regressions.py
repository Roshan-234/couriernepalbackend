"""Security and business regressions across the actual REST endpoints."""
import pytest
from app.extensions import db
from app.models.user import User, UserStatus
from app.models.blog import BlogCategory, BlogPost
from tests.conftest import auth_header


@pytest.fixture
def account(app):
    import uuid
    client = app.test_client()
    email = uuid.uuid4().hex + '@example.test'
    result = client.post('/api/auth/register', json=dict(email=email, password='Customer123!', first_name='Test', last_name='Customer'))
    assert result.status_code == 201
    assert result.json['user']['roles'] == ['customer']
    login = client.post('/api/auth/login', json={'email': email, 'password': 'Customer123!'})
    assert login.status_code == 200
    return client, login.json, email


def test_logout_revokes_refresh_family(account):
    client, login, _ = account
    refresh = auth_header(login['refresh_token'])
    updated = client.post('/api/auth/refresh', headers=refresh).json['access_token']
    assert client.post('/api/auth/logout', headers=auth_header(updated)).status_code == 200
    assert client.get('/api/auth/profile', headers=auth_header(login['access_token'])).status_code == 401
    assert client.post('/api/auth/refresh', headers=refresh).status_code == 401


def test_revoke_session_blocks_refresh(account):
    client, login, _ = account
    headers = auth_header(login['access_token'])
    sid = client.get('/api/sessions', headers=headers).json[0]['id']
    assert client.delete(f'/api/sessions/{sid}', headers=headers).status_code == 200
    assert client.post('/api/auth/refresh', headers=auth_header(login['refresh_token'])).status_code == 401


def test_inactive_account_is_denied_immediately(app, account):
    client, login, email = account
    user = User.query.filter_by(email=email).one()
    user.status = UserStatus.SUSPENDED
    db.session.commit()
    assert client.get('/api/auth/profile', headers=auth_header(login['access_token'])).status_code == 401


def test_password_reset_revokes_old_sessions(app, account):
    client, login, email = account
    token = User.query.filter_by(email=email).one().generate_reset_token()
    assert client.post('/api/auth/password-reset/confirm', json={'token': token, 'password': 'alllowercase'}).status_code == 400
    assert client.post('/api/auth/password-reset/confirm', json={'token': token, 'password': 'Updated123!'}).status_code == 200
    assert client.post('/api/auth/refresh', headers=auth_header(login['refresh_token'])).status_code == 401
    assert client.post('/api/auth/password-reset/confirm', json={'token': token, 'password': 'Updated123!'}).status_code == 400


@pytest.mark.parametrize('path', ['/services', '/warehouses', '/faq', '/blog/posts', '/blog/categories', '/blog/tags', '/agents'])
def test_customer_cannot_edit_operational_data(account, path):
    client, login, _ = account
    assert client.post('/api' + path, json={}, headers=auth_header(login['access_token'])).status_code == 403


@pytest.mark.parametrize('extra', [dict(declared_value=-5), dict(total_packages=True), dict(total_weight_kg=None),
    dict(parcels=[{'weight_kg': -2}]), dict(service_id=999999), dict(origin='x' * 256), dict(parcels=None)])
def test_booking_rejects_invalid_data(account, extra):
    client, login, _ = account
    payload = dict(origin='Kathmandu', destination='Pokhara', **{k:v for k,v in extra.items() if k != 'origin'})
    if 'origin' in extra:
        payload['origin'] = extra['origin']
    result = client.post('/api/shipments', json=payload, headers=auth_header(login['access_token']))
    assert result.status_code == 400, result.json


def test_tracking_validates_both_update_paths(account, admin_token):
    client, login, _ = account
    created = client.post('/api/shipments', json=dict(origin='A', destination='B'), headers=auth_header(login['access_token'])).json
    assert created['estimated_delivery'] is None
    assert client.put(f"/api/shipments/{created['id']}", json={'status': 'invented'}, headers=auth_header(admin_token)).status_code == 400
    assert client.post(f"/api/tracking/{created['tracking_no']}/events", json={'status': 'invented'}, headers=auth_header(admin_token)).status_code == 400


def test_weight_brackets_and_pricing_validation(client, admin_token):
    route = dict(service_type='regression', shipment_type='parcel', origin_country='Nepal', destination_country='Testland')
    headers = auth_header(admin_token)
    for minimum, maximum, base in [(0, 5, 100), (6, 20, 200)]:
        result = client.post('/api/pricing/rules', headers=headers, json=dict(**route, min_weight=minimum, max_weight=maximum, base_price=base, price_per_kg=10))
        assert result.status_code == 201, result.json
    assert client.post('/api/pricing/calculate', json=dict(**route, weight=10)).json['total_cost'] == 300
    assert client.post('/api/pricing/calculate', json=dict(**route, weight=30)).status_code == 404
    assert client.post('/api/pricing/rules', headers=headers, json=dict(**route, base_price=-1, price_per_kg=10)).status_code == 400
    assert client.post('/api/pricing/rules', headers=headers, json=dict(**route, base_price=1, price_per_kg=10, id=77)).status_code == 400


def test_draft_blog_not_public(app, account, admin_token):
    client, login, _ = account
    category = BlogCategory(name='Private regression', slug='private-regression')
    db.session.add(category)
    db.session.flush()
    post = BlogPost(title='Draft', slug='private-regression', content='Unpublished', category_id=category.id, author_id=login['user']['id'], status='draft')
    db.session.add(post)
    db.session.commit()
    public = app.test_client()
    assert public.get('/api/blog/posts?status=all').status_code == 401
    assert public.get('/api/blog/posts/private-regression').status_code == 401
    assert client.get('/api/blog/posts?status=all', headers=auth_header(login['access_token'])).status_code == 403
    assert client.get('/api/blog/posts?status=all', headers=auth_header(admin_token)).status_code == 200
    assert public.get('/api/blog/posts?page=bad').status_code == 400


def test_dashboard_does_not_invent_expenses(client, admin_token):
    result = client.get('/api/admin/dashboard/revenue-analytics', headers=auth_header(admin_token))
    assert result.status_code == 200
    assert result.json['expenses'] == [None] * 12
    assert client.get('/api/admin/dashboard/stats', headers=auth_header(admin_token)).status_code == 200


@pytest.mark.parametrize('endpoint', ['invoices', 'packing-lists'])
def test_document_cannot_link_another_customers_shipment(app, account, admin_token, endpoint):
    client, login, _ = account
    shipment = client.post('/api/shipments', headers=auth_header(admin_token), json={'origin': 'A', 'destination': 'B'}).json
    result = client.post('/api/' + endpoint, headers=auth_header(login['access_token']), json={'shipment_id': shipment['id']})
    assert result.status_code == 403
    assert client.post('/api/' + endpoint, headers=auth_header(login['access_token']), json={'items': [{'quantity': -1}]}).status_code == 400


def test_invoice_totals_computed_on_server(account):
    client, login, _ = account
    headers = auth_header(login['access_token'])
    result = client.post('/api/invoices', headers=headers, json={'seller_name': 'Seller', 'buyer_name': 'Buyer', 'items': [{'quantity': 2, 'unit_price': 10, 'total_price': 1}], 'tax_percentage': 10})
    assert result.status_code == 201
    assert result.json['total_amount'] == 22
    assert client.put(f"/api/invoices/{result.json['id']}", headers=headers, json={'status': 'paid'}).status_code == 403


def test_account_cannot_disable_itself(client, admin_token):
    me = client.get('/api/auth/profile', headers=auth_header(admin_token)).json
    assert client.put(f"/api/users/{me['id']}", json={'status': 'suspended'}, headers=auth_header(admin_token)).status_code == 400
    assert client.get('/api/auth/profile', headers=auth_header(admin_token)).json['status'] == 'active'


def test_comments_replies_and_ownership(app, account, admin_token):
    client, login, _ = account
    headers = auth_header(login['access_token'])
    category = BlogCategory(name='Comment tests', slug='comments-category')
    db.session.add(category)
    db.session.flush()
    post = BlogPost(title='Comments', slug='comment-regression', content='Published', category_id=category.id, author_id=login['user']['id'], status='published')
    db.session.add(post)
    db.session.commit()
    path = '/api/blog/posts/comment-regression/comments'
    comment = client.post(path, headers=headers, json={'content': 'Original'}).json
    assert comment['author']['id'] == login['user']['id']
    reply = client.post(path, headers=auth_header(admin_token), json={'content': 'Reply', 'parent_id': comment['id']})
    assert reply.status_code == 201
    assert client.get(path).json[0]['replies'][0]['content'] == 'Reply'
    assert client.put(f"{path}/{reply.json['id']}", headers=headers, json={'content': 'Overwrite'}).status_code == 403
    assert client.delete(f"{path}/{reply.json['id']}", headers=headers).status_code == 403
    assert client.post(path, headers=headers, json={'content': 'Bad', 'parent_id': 999999}).status_code == 400


def test_admin_bootstrap_command(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=['create-admin', '--email', 'bootstrap@example.test', '--password', 'Bootstrap123!'])
    assert result.exit_code == 0, result.output
    assert User.query.filter_by(email='bootstrap@example.test').one().is_admin
