"""Branch, rider, pickup and delivery operations.

These cover the behaviour the endpoints exist to guarantee — ownership
isolation, assignment restrictions, the status state machine and OTP
secrecy — rather than the shape of a 200.
"""
import uuid
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models.branch import Branch
from app.models.delivery import ProofOfDelivery
from app.models.shipment import Shipment
from app.models.user import User
from tests.conftest import auth_header, register_and_login

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def _email():
    return f"ops-{uuid.uuid4().hex[:10]}@example.test"


def _customer(client):
    """A fresh customer account: (token, user_id)."""
    email = _email()
    token = register_and_login(client, email)
    return token, User.query.filter_by(email=email).first().id


@pytest.fixture
def admin(admin_token):
    return auth_header(admin_token)


@pytest.fixture
def branch(client, admin):
    """An active Kathmandu branch that also serves Lalitpur."""
    code = f"KTM{uuid.uuid4().hex[:6].upper()}"
    result = client.post('/api/branches/', headers=admin, json={
        'code': code, 'name': 'Kathmandu Hub', 'type': 'hub',
        'district': 'Kathmandu', 'city': 'Kathmandu',
        'serves_districts': ['Lalitpur', 'Bhaktapur'],
    })
    assert result.status_code == 201, result.json
    return result.json


def _make_rider(client, admin, branch_id=None):
    """Register a user and give them a rider profile. Returns (headers, rider_id)."""
    token, user_id = _customer(client)
    payload = {'user_id': user_id, 'vehicle_type': 'bike', 'status': 'available'}
    if branch_id:
        payload['branch_id'] = branch_id
    result = client.post('/api/riders/', headers=admin, json=payload)
    assert result.status_code == 201, result.json
    return auth_header(token), result.json['id']


def _make_shipment(client, token, status='pending', rider_id=None):
    """Book a shipment through the API, then force operational state in the DB."""
    result = client.post('/api/shipments/', headers=auth_header(token), json={
        'origin': 'Kathmandu', 'destination': 'Pokhara',
        'receiver_name': 'Sita Rai', 'total_packages': 1,
    })
    assert result.status_code == 201, result.json
    shipment = db.session.get(Shipment, result.json['id'])
    shipment.status = status
    shipment.rider_id = rider_id
    shipment.destination_district = 'Kaski'
    db.session.commit()
    return shipment.id


# ── geo ──────────────────────────────────────────────────────────────────────

def test_geo_serves_all_77_districts(client):
    result = client.get('/api/geo/districts')
    assert result.status_code == 200
    assert result.json['total'] == 77
    assert len(result.json['items']) == 77
    names = {d['name'] for d in result.json['items']}
    assert {'Kathmandu', 'Kaski', 'Jhapa', 'Humla'} <= names


def test_geo_filters_and_lookup(client):
    assert client.get('/api/geo/provinces').json['total'] == 7
    bagmati = client.get('/api/geo/districts?province=Bagmati').json
    assert bagmati['total'] == 13
    assert all(d['province'] == 'Bagmati' for d in bagmati['items'])
    valley = client.get('/api/geo/districts?zone=valley').json
    assert {d['name'] for d in valley['items']} == {'Kathmandu', 'Lalitpur', 'Bhaktapur'}
    one = client.get('/api/geo/districts/kathmandu')
    assert one.status_code == 200 and one.json['headquarters'] == 'Kathmandu'
    assert client.get('/api/geo/districts/atlantis').status_code == 404
    assert client.get('/api/geo/zones').json['total'] == 4


# ── branches ─────────────────────────────────────────────────────────────────

def test_branch_rejects_unknown_district(client, admin):
    result = client.post('/api/branches/', headers=admin, json={
        'code': f"X{uuid.uuid4().hex[:6].upper()}", 'name': 'Nowhere Depot',
        'district': 'Atlantis',
    })
    assert result.status_code == 400
    assert 'district' in str(result.json).lower()


def test_branch_rejects_unknown_served_district(client, admin):
    result = client.post('/api/branches/', headers=admin, json={
        'code': f"X{uuid.uuid4().hex[:6].upper()}", 'name': 'Bad Coverage',
        'district': 'Kathmandu', 'serves_districts': ['Kathmandu', 'Narnia'],
    })
    assert result.status_code == 400


def test_branch_rejects_unknown_type(client, admin):
    result = client.post('/api/branches/', headers=admin, json={
        'code': f"X{uuid.uuid4().hex[:6].upper()}", 'name': 'Odd Type',
        'district': 'Kathmandu', 'type': 'spaceport',
    })
    assert result.status_code == 400


def test_branch_creation_is_admin_only(client):
    token, _ = _customer(client)
    result = client.post('/api/branches/', headers=auth_header(token), json={
        'code': 'NOPE1', 'name': 'Customer Branch', 'district': 'Kathmandu',
    })
    assert result.status_code == 403


def test_branch_is_public_readable_by_code_and_id(client, branch):
    by_code = client.get(f"/api/branches/{branch['code']}")
    by_id = client.get(f"/api/branches/{branch['id']}")
    assert by_code.status_code == 200 and by_id.status_code == 200
    assert by_code.json['id'] == by_id.json['id'] == branch['id']
    # Province is derived from the district rather than trusted from input.
    assert by_code.json['province'] == 'Bagmati'


def test_branch_serving_lookup_uses_coverage_list(client, branch):
    result = client.get('/api/branches/serving/lalitpur')
    assert result.status_code == 200
    assert branch['id'] in [b['id'] for b in result.json['items']]
    # A district the branch does not cover must not list it.
    uncovered = client.get('/api/branches/serving/humla')
    assert branch['id'] not in [b['id'] for b in uncovered.json['items']]
    assert client.get('/api/branches/serving/atlantis').status_code == 404


def test_branch_deactivation_is_soft(client, admin, branch):
    assert client.delete(f"/api/branches/{branch['code']}", headers=admin).status_code == 200
    listed = client.get('/api/branches/').json['items']
    assert branch['id'] not in [b['id'] for b in listed]
    # The row survives, so shipments and riders still resolve it.
    assert db.session.get(Branch, branch['id']) is not None
    assert client.get(f"/api/branches/{branch['code']}").json['is_active'] is False


# ── riders ───────────────────────────────────────────────────────────────────

def test_rider_me_requires_a_rider_profile(client):
    token, _ = _customer(client)
    assert client.get('/api/riders/me', headers=auth_header(token)).status_code == 403


def test_rider_profile_is_created_once_per_user(client, admin, branch):
    token, user_id = _customer(client)
    first = client.post('/api/riders/', headers=admin,
                        json={'user_id': user_id, 'branch_id': branch['id']})
    assert first.status_code == 201
    assert first.json['rider_code']          # generated when omitted
    second = client.post('/api/riders/', headers=admin, json={'user_id': user_id})
    assert second.status_code == 409


def test_rider_self_service_status_and_location(client, admin):
    headers, rider_id = _make_rider(client, admin)
    assert client.get('/api/riders/me', headers=headers).json['id'] == rider_id

    bad = client.patch('/api/riders/me/status', headers=headers, json={'status': 'napping'})
    assert bad.status_code == 400
    good = client.patch('/api/riders/me/status', headers=headers, json={'status': 'on_duty'})
    assert good.status_code == 200 and good.json['can_take_work'] is True

    located = client.post('/api/riders/me/location', headers=headers,
                          json={'latitude': 27.7172, 'longitude': 85.3240})
    assert located.status_code == 200 and located.json['location_updated_at']
    assert client.post('/api/riders/me/location', headers=headers,
                       json={'latitude': 999, 'longitude': 0}).status_code == 400


def test_rider_shipment_queue_only_shows_own_assignments(client, admin):
    mine, rider_id = _make_rider(client, admin)
    theirs, other_id = _make_rider(client, admin)
    token, _ = _customer(client)
    shipment_id = _make_shipment(client, token, rider_id=rider_id)

    assert shipment_id in [s['id'] for s in client.get('/api/riders/me/shipments', headers=mine).json['items']]
    assert shipment_id not in [s['id'] for s in client.get('/api/riders/me/shipments', headers=theirs).json['items']]


def test_rider_list_is_admin_only(client):
    token, _ = _customer(client)
    assert client.get('/api/riders/', headers=auth_header(token)).status_code == 403


# ── pickups ──────────────────────────────────────────────────────────────────

def _request_pickup(client, token, district='Kathmandu'):
    result = client.post('/api/pickups/', headers=auth_header(token), json={
        'contact_name': 'Hari Thapa', 'contact_phone': '9800000000',
        'address': 'Jhamsikhel, Ward 3', 'district': district,
        'scheduled_date': TOMORROW, 'time_slot': 'morning', 'package_count': 4,
    })
    assert result.status_code == 201, result.json
    assert result.json['status'] == 'requested'
    return result.json


def test_pickup_rejects_unknown_district(client):
    token, _ = _customer(client)
    result = client.post('/api/pickups/', headers=auth_header(token), json={
        'contact_name': 'Hari Thapa', 'contact_phone': '9800000000',
        'address': 'Nowhere', 'district': 'Atlantis',
    })
    assert result.status_code == 400


def test_customer_cannot_read_or_cancel_another_customers_pickup(client):
    token_a, _ = _customer(client)
    token_b, _ = _customer(client)
    pickup = _request_pickup(client, token_a)

    assert client.get(f"/api/pickups/{pickup['id']}", headers=auth_header(token_a)).status_code == 200
    assert client.get(f"/api/pickups/{pickup['id']}", headers=auth_header(token_b)).status_code == 403
    assert client.post(f"/api/pickups/{pickup['id']}/cancel", headers=auth_header(token_b)).status_code == 403

    mine = client.get('/api/pickups/my', headers=auth_header(token_a)).json['items']
    theirs = client.get('/api/pickups/my', headers=auth_header(token_b)).json['items']
    assert pickup['id'] in [p['id'] for p in mine]
    assert pickup['id'] not in [p['id'] for p in theirs]

    cancelled = client.post(f"/api/pickups/{pickup['id']}/cancel", headers=auth_header(token_a))
    assert cancelled.status_code == 200 and cancelled.json['status'] == 'cancelled'
    # A closed pickup cannot be cancelled twice.
    assert client.post(f"/api/pickups/{pickup['id']}/cancel", headers=auth_header(token_a)).status_code == 409


def test_pickup_assignment_is_admin_only_and_binds_the_rider(client, admin):
    token, _ = _customer(client)
    pickup = _request_pickup(client, token)
    rider_headers, rider_id = _make_rider(client, admin)
    other_headers, _ = _make_rider(client, admin)

    refused = client.post(f"/api/pickups/{pickup['id']}/assign",
                          headers=auth_header(token), json={'rider_id': rider_id})
    assert refused.status_code == 403

    assigned = client.post(f"/api/pickups/{pickup['id']}/assign", headers=admin,
                           json={'rider_id': rider_id})
    assert assigned.status_code == 200
    assert assigned.json['status'] == 'assigned'
    assert assigned.json['rider_id'] == rider_id and assigned.json['assigned_at']

    queue = client.get('/api/pickups/assigned', headers=rider_headers).json['items']
    assert pickup['id'] in [p['id'] for p in queue]
    assert pickup['id'] not in [p['id'] for p in
                                client.get('/api/pickups/assigned', headers=other_headers).json['items']]

    # Only the rider holding the job may close it.
    assert client.post(f"/api/pickups/{pickup['id']}/complete", headers=other_headers,
                       json={'collected_count': 4}).status_code == 403
    done = client.post(f"/api/pickups/{pickup['id']}/complete", headers=rider_headers,
                       json={'collected_count': 3})
    assert done.status_code == 200
    assert done.json['status'] == 'completed' and done.json['collected_count'] == 3


def test_pickup_reschedule_rejects_a_past_date(client, admin):
    token, _ = _customer(client)
    pickup = _request_pickup(client, token)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert client.post(f"/api/pickups/{pickup['id']}/reschedule", headers=admin,
                       json={'scheduled_date': yesterday}).status_code == 400
    later = (date.today() + timedelta(days=3)).isoformat()
    moved = client.post(f"/api/pickups/{pickup['id']}/reschedule", headers=admin,
                        json={'scheduled_date': later, 'time_slot': 'evening'})
    assert moved.status_code == 200 and moved.json['scheduled_date'] == later


# ── deliveries ───────────────────────────────────────────────────────────────

def test_attempt_rejects_an_illegal_status_transition(client, admin):
    headers, rider_id = _make_rider(client, admin)
    token, _ = _customer(client)
    # A shipment still awaiting pickup cannot jump to a delivery outcome.
    shipment_id = _make_shipment(client, token, status='pending', rider_id=rider_id)
    result = client.post(f'/api/deliveries/shipments/{shipment_id}/attempt', headers=headers,
                         json={'outcome': 'failed', 'reason_code': 'recipient_unavailable'})
    assert result.status_code == 409
    assert db.session.get(Shipment, shipment_id).status == 'pending'
    assert db.session.get(Shipment, shipment_id).delivery_attempt_count in (0, None)


def test_rider_may_only_record_attempts_on_own_shipments(client, admin):
    _assigned, rider_id = _make_rider(client, admin)
    intruder, _ = _make_rider(client, admin)
    token, _ = _customer(client)
    shipment_id = _make_shipment(client, token, status='out_for_delivery', rider_id=rider_id)
    result = client.post(f'/api/deliveries/shipments/{shipment_id}/attempt', headers=intruder,
                         json={'outcome': 'failed', 'reason_code': 'address_incorrect'})
    assert result.status_code == 403


def test_failed_attempt_counts_logs_and_leaves_room_for_rto(client, admin):
    headers, rider_id = _make_rider(client, admin)
    token, _ = _customer(client)
    shipment_id = _make_shipment(client, token, status='out_for_delivery', rider_id=rider_id)

    result = client.post(f'/api/deliveries/shipments/{shipment_id}/attempt', headers=headers,
                         json={'outcome': 'failed', 'reason_code': 'recipient_unavailable',
                               'reason_note': 'Nobody home'})
    assert result.status_code == 201
    assert result.json['shipment_status'] == 'delivery_attempted'
    assert result.json['delivery_attempt_count'] == 1
    assert result.json['attempt']['attempt_no'] == 1
    assert 'rto_initiated' in result.json['next_statuses']

    shipment = db.session.get(Shipment, shipment_id)
    assert shipment.delivery_attempt_count == 1
    # The customer timeline records the attempt.
    assert 'delivery_attempted' in [e.status for e in shipment.events]

    listed = client.get(f'/api/deliveries/shipments/{shipment_id}/attempts', headers=headers)
    assert listed.status_code == 200 and listed.json['total'] == 1

    # An unrelated customer cannot read the attempt history.
    stranger, _ = _customer(client)
    assert client.get(f'/api/deliveries/shipments/{shipment_id}/attempts',
                      headers=auth_header(stranger)).status_code == 403
    # The shipment owner can.
    assert client.get(f'/api/deliveries/shipments/{shipment_id}/attempts',
                      headers=auth_header(token)).status_code == 200

    rto = client.post(f'/api/deliveries/shipments/{shipment_id}/rto', headers=admin,
                      json={'rto_reason': 'Three failed attempts'})
    assert rto.status_code == 200 and rto.json['status'] == 'rto_initiated'
    assert 'rto_initiated' in [e.status for e in db.session.get(Shipment, shipment_id).events]


def test_rto_is_admin_only_and_respects_the_state_machine(client, admin):
    headers, rider_id = _make_rider(client, admin)
    token, _ = _customer(client)
    shipment_id = _make_shipment(client, token, status='out_for_delivery', rider_id=rider_id)
    assert client.post(f'/api/deliveries/shipments/{shipment_id}/rto', headers=headers,
                       json={'rto_reason': 'rider says so'}).status_code == 403
    # out_for_delivery has no edge to rto_initiated; an attempt must be logged first.
    assert client.post(f'/api/deliveries/shipments/{shipment_id}/rto', headers=admin,
                       json={'rto_reason': 'too soon'}).status_code == 409


def test_otp_never_appears_in_a_response_and_gates_delivery(client, admin, monkeypatch):
    import app.deliveries.routes as delivery_routes
    monkeypatch.setattr(delivery_routes, 'randbelow', lambda bound: 424242)

    headers, rider_id = _make_rider(client, admin)
    token, _ = _customer(client)
    shipment_id = _make_shipment(client, token, status='out_for_delivery', rider_id=rider_id)

    issued = client.post(f'/api/deliveries/shipments/{shipment_id}/otp', headers=headers)
    assert issued.status_code == 201
    body = issued.get_data(as_text=True)
    assert '424242' not in body, f'OTP leaked in the response: {body}'
    assert 'otp_hash' not in body
    assert set(issued.json) == {'msg', 'shipment_id', 'otp_required', 'expires_at'}

    # Only a hash reaches the database.
    proof = ProofOfDelivery.query.filter_by(shipment_id=shipment_id).first()
    assert proof.otp_hash and '424242' not in proof.otp_hash

    # The attempt route must not be usable to walk around the code.
    assert client.post(f'/api/deliveries/shipments/{shipment_id}/attempt', headers=headers,
                       json={'outcome': 'delivered'}).status_code == 409
    assert client.post(f'/api/deliveries/shipments/{shipment_id}/pod', headers=headers,
                       json={'recipient_name': 'Sita Rai'}).status_code == 400
    assert client.post(f'/api/deliveries/shipments/{shipment_id}/pod', headers=headers,
                       json={'recipient_name': 'Sita Rai', 'otp': '000000'}).status_code == 400
    assert db.session.get(Shipment, shipment_id).status == 'out_for_delivery'

    accepted = client.post(f'/api/deliveries/shipments/{shipment_id}/pod', headers=headers,
                           json={'recipient_name': 'Sita Rai', 'recipient_relation': 'family',
                                 'otp': '424242', 'photo_path': 'storage/pod/1.jpg'})
    assert accepted.status_code == 201, accepted.json
    assert accepted.json['shipment_status'] == 'delivered'
    assert accepted.json['proof']['otp_verified'] is True
    assert '424242' not in accepted.get_data(as_text=True)

    shipment = db.session.get(Shipment, shipment_id)
    assert shipment.status == 'delivered' and shipment.delivered_at is not None
    assert 'delivered' in [e.status for e in shipment.events]


def test_pod_without_an_otp_still_requires_a_legal_transition(client, admin):
    headers, rider_id = _make_rider(client, admin)
    token, _ = _customer(client)
    too_early = _make_shipment(client, token, status='pending', rider_id=rider_id)
    assert client.post(f'/api/deliveries/shipments/{too_early}/pod', headers=headers,
                       json={'recipient_name': 'Sita Rai'}).status_code == 409
    assert db.session.get(Shipment, too_early).status == 'pending'

    ready = _make_shipment(client, token, status='out_for_delivery', rider_id=rider_id)
    result = client.post(f'/api/deliveries/shipments/{ready}/pod', headers=headers,
                         json={'recipient_name': 'Sita Rai'})
    assert result.status_code == 201
    assert result.json['proof']['otp_required'] is False
    assert db.session.get(Shipment, ready).status == 'delivered'
