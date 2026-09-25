"""Regression coverage for the same-origin browser's customer journey."""
import pytest
from app.extensions import db
from app.models.user import User, UserStatus
from app.models.role import Role


@pytest.fixture
def browser(app):
    client = app.test_client()
    with app.app_context():
        user = User.query.filter_by(email='browser@example.test').first()
        if not user:
            user = User(username='browser', email='browser@example.test',
                        first_name='Browser', last_name='Test', status=UserStatus.ACTIVE)
            user.set_password('Browser123!')
            user.roles.append(Role.query.filter_by(name='customer').one())
            db.session.add(user)
            db.session.commit()
    response = client.post('/api/auth/login', json={'email': 'browser@example.test', 'password': 'Browser123!'})
    assert response.status_code == 200
    return client


def csrf(client):
    return {'X-CSRF-TOKEN': client.get_cookie('csrf_access_token').value}


def test_cookie_booking_list_and_public_tracking(browser):
    payload = {'origin': 'Kathmandu', 'destination': 'Pokhara', 'total_weight_kg': 2.5,
               'total_packages': 1, 'sender_name': 'Test Sender', 'receiver_name': 'Test Receiver'}
    assert browser.post('/api/shipments', json=payload).status_code == 401
    response = browser.post('/api/shipments', json=payload, headers=csrf(browser))
    assert response.status_code == 201
    shipment = response.get_json()
    assert shipment['tracking_no']
    assert shipment['events'][0]['status'] == 'pending'
    assert any(row['id'] == shipment['id'] for row in browser.get('/api/shipments/my').get_json())
    public = browser.application.test_client()
    tracked = public.get('/api/tracking/' + shipment['tracking_no'])
    assert tracked.status_code == 200
    assert tracked.get_json()['tracking_no'] == shipment['tracking_no']
    assert 'sender_name' not in tracked.get_json()
    assert public.get('/api/tracking/DOES-NOT-EXIST').status_code == 404
    assert public.get('/api/shipments/' + str(shipment['id'])).status_code == 401


@pytest.mark.parametrize('payload', [ {}, {'origin': ' ', 'destination': 'Pokhara'},
    {'origin': 'Kathmandu', 'destination': 'Pokhara', 'total_weight_kg': -1},
    {'origin': 'Kathmandu', 'destination': 'Pokhara', 'total_packages': 0} ])
def test_invalid_booking_returns_400(browser, payload):
    assert browser.post('/api/shipments', json=payload, headers=csrf(browser)).status_code == 400


def test_profile_update_and_customer_permissions(browser):
    response = browser.patch('/api/auth/profile', json={'first_name': 'Updated', 'last_name': 'Customer', 'roles': ['super_admin']}, headers=csrf(browser))
    assert response.status_code == 200
    assert response.get_json()['roles'] == ['customer']
    assert browser.get('/api/auth/profile').get_json()['first_name'] == 'Updated'
    assert browser.get('/api/contact').status_code == 403
    assert browser.get('/api/shipments').status_code == 403
    assert browser.post('/api/pricing', json={}, headers=csrf(browser)).status_code == 403


def test_public_contact_and_validation(app):
    client = app.test_client()
    assert client.post('/api/contact', json={}).status_code == 400
    assert client.post('/api/contact', json={'name': '', 'email': 'invalid', 'message': 'Hello'}).status_code == 400
    result = client.post('/api/contact', json={'name': 'Test', 'email': 'test@example.test', 'subject': 'Shipping', 'message': 'Please advise on shipping.'})
    assert result.status_code == 201
    assert result.get_json()['id']


def test_refresh_and_logout(browser):
    refreshed = browser.post('/api/auth/refresh', json={}, headers={'X-CSRF-TOKEN': browser.get_cookie('csrf_refresh_token').value})
    assert refreshed.status_code == 200
    assert browser.get('/api/auth/profile').status_code == 200
    assert browser.post('/api/auth/logout', json={}, headers=csrf(browser)).status_code == 200
    assert browser.get('/api/auth/profile').status_code == 401
    assert browser.get_cookie('access_token') is None
    assert browser.get_cookie('refresh_token') is None


def test_auth_missing_fields_are_client_errors(app):
    client = app.test_client()
    assert client.post('/api/auth/login', json={}).status_code == 400
    assert client.post('/api/auth/register', json={}).status_code == 400


def test_admin_status_update_reaches_public_tracking(browser, admin_token):
    result = browser.post('/api/shipments', json={'origin': 'Kathmandu', 'destination': 'Pokhara'}, headers=csrf(browser))
    shipment = result.get_json()
    endpoint = '/api/shipments/' + str(shipment['id'])
    update = {'status': 'in_transit', 'location': 'Kathmandu hub', 'description': 'Departed hub'}
    assert browser.put(endpoint, json=update, headers=csrf(browser)).status_code == 403
    headers = {'Authorization': 'Bearer ' + admin_token}
    assert browser.put(endpoint, json={}, headers=headers).status_code == 400
    assert browser.put(endpoint, json=update, headers=headers).status_code == 200
    tracking = browser.get('/api/tracking/' + shipment['tracking_no']).get_json()
    assert tracking['status'] == 'in_transit'
    assert any(event['description'] == 'Departed hub' for event in tracking['events'])
