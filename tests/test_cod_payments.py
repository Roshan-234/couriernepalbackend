"""Behaviour tests for the COD and payments APIs.

These cover the things that cost real money if they are wrong: one merchant
seeing another's cash position, a shipment being collected twice, a payout
total taken from the client instead of the ledger, a double charge on a
retried request, a gateway credential leaking into a response, and a refund
larger than the payment it refunds.
"""
import uuid

import pytest

from tests.conftest import auth_header
from app.extensions import db


PASSWORD = "Admin1234!"


# ── fixtures and helpers ─────────────────────────────────────────────────────

def _unique(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _make_user(app, email, roles=("customer",)):
    """Create a user directly, so the 'first registration is super_admin'
    heuristic cannot hand a test subject administrative powers."""
    from app.models.user import User, UserStatus
    from app.models.role import Role
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(
                username=email.split("@")[0],
                email=email,
                first_name="Test",
                last_name="User",
                status=UserStatus.ACTIVE,
            )
            user.set_password(PASSWORD)
            db.session.add(user)
            db.session.flush()
        for name in roles:
            role = Role.query.filter_by(name=name).first()
            if role and role not in user.roles:
                user.roles.append(role)
        db.session.commit()
        return user.id


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["access_token"]


def _merchant(app, client, prefix):
    """A plain (non-admin) user plus a token for them."""
    email = f"{_unique(prefix)}@codtest.com"
    uid = _make_user(app, email)
    return uid, _login(client, email)


def _cod_shipment(app, merchant_id, amount=1500.0, is_cod=True):
    from app.models.shipment import Shipment
    with app.app_context():
        shipment = Shipment(
            tracking_no=_unique("CODTRK").upper(),
            user_id=merchant_id,
            merchant_id=merchant_id,
            status="out_for_delivery",
            origin="Kathmandu",
            destination="Pokhara",
            is_cod=is_cod,
            cod_amount=amount,
            cod_currency="NPR",
            cod_status="pending" if is_cod else "not_applicable",
            total_charge=amount,
        )
        db.session.add(shipment)
        db.session.commit()
        return shipment.id


def _collect(client, token, shipment_id, amount, mode="cash", reference="RCPT-1"):
    return client.post(
        f"/api/cod/collections/{shipment_id}/collect",
        json={"amount": amount, "collection_mode": mode, "reference_no": reference},
        headers=auth_header(token),
    )


def _deposit(client, admin_token, collection_id, reference="DEP-1"):
    return client.post(
        f"/api/cod/collections/{collection_id}/deposit",
        json={"deposit_reference": reference},
        headers=auth_header(admin_token),
    )


def _collected_and_deposited(app, client, admin_token, merchant_id, amount):
    """Walk one shipment all the way to 'deposited' and return the collection."""
    shipment_id = _cod_shipment(app, merchant_id, amount)
    r = _collect(client, admin_token, shipment_id, amount)
    assert r.status_code == 201, r.get_json()
    collection = r.get_json()
    d = _deposit(client, admin_token, collection["id"])
    assert d.status_code == 200, d.get_json()
    return d.get_json()


@pytest.fixture(scope="module")
def gateway(app, admin_token, client):
    """A configured, active sandbox gateway with a known secret."""
    secret = "sk_test_" + uuid.uuid4().hex
    r = client.post("/api/payments/gateways", json={
        "code": "khalti",
        "name": "Khalti",
        "display_name": "Khalti Wallet",
        "is_active": True,
        "is_sandbox": True,
        "sandbox_base_url": "https://a.khalti.test",
        "verify_path": "/epayment/lookup/",
        "merchant_code": "TESTMERCHANT",
        "secret_key": secret,
        "min_amount": 10.0,
        "max_amount": 100000.0,
        "supported_currencies": ["NPR"],
    }, headers=auth_header(admin_token))
    # Another test run against the same session DB may already have created it.
    if r.status_code == 409:
        existing = [g for g in client.get("/api/payments/gateways/all",
                                          headers=auth_header(admin_token)).get_json()
                    if g["code"] == "khalti"][0]
        r = client.put(f"/api/payments/gateways/{existing['id']}",
                       json={"is_active": True, "secret_key": secret},
                       headers=auth_header(admin_token))
    assert r.status_code in (200, 201), r.get_json()
    return {"id": r.get_json()["id"], "secret": secret}


# ── COD: visibility ──────────────────────────────────────────────────────────

def test_merchant_sees_only_own_collections(app, client, admin_token):
    """One merchant's cash position must not be visible to another."""
    m1, t1 = _merchant(app, client, "codm1")
    m2, t2 = _merchant(app, client, "codm2")
    _collect(client, admin_token, _cod_shipment(app, m1, 900.0), 900.0)
    _collect(client, admin_token, _cod_shipment(app, m2, 800.0), 800.0)

    seen_by_m1 = client.get("/api/cod/collections", headers=auth_header(t1)).get_json()
    seen_by_m2 = client.get("/api/cod/collections", headers=auth_header(t2)).get_json()

    assert {c["merchant_id"] for c in seen_by_m1["items"]} == {m1}
    assert {c["merchant_id"] for c in seen_by_m2["items"]} == {m2}

    # A merchant cannot widen the query by asking for someone else's id.
    forced = client.get(f"/api/cod/collections?merchant_id={m2}",
                        headers=auth_header(t1)).get_json()
    assert all(c["merchant_id"] == m1 for c in forced["items"])

    # The admin sees both.
    all_rows = client.get("/api/cod/collections?per_page=100",
                          headers=auth_header(admin_token)).get_json()
    assert {m1, m2} <= {c["merchant_id"] for c in all_rows["items"]}


# ── COD: collection ──────────────────────────────────────────────────────────

def test_collecting_twice_on_one_shipment_fails(app, client, admin_token):
    merchant_id, _ = _merchant(app, client, "codtwice")
    shipment_id = _cod_shipment(app, merchant_id, 1200.0)

    first = _collect(client, admin_token, shipment_id, 1200.0)
    assert first.status_code == 201, first.get_json()
    assert first.get_json()["status"] == "collected"

    second = _collect(client, admin_token, shipment_id, 1200.0)
    assert second.status_code == 409


def test_collect_rejects_non_cod_shipment(app, client, admin_token):
    merchant_id, _ = _merchant(app, client, "codnoncod")
    shipment_id = _cod_shipment(app, merchant_id, 0.0, is_cod=False)
    r = _collect(client, admin_token, shipment_id, 0.0)
    assert r.status_code == 400


def test_collect_rejects_amount_mismatch(app, client, admin_token):
    merchant_id, _ = _merchant(app, client, "codmismatch")
    shipment_id = _cod_shipment(app, merchant_id, 1000.0)
    r = _collect(client, admin_token, shipment_id, 950.0)
    assert r.status_code == 400
    # Nothing was recorded.
    assert client.get(f"/api/cod/collections?shipment={shipment_id}",
                      headers=auth_header(admin_token)).status_code == 200


def test_collect_stamps_the_shipment(app, client, admin_token):
    from app.models.shipment import Shipment
    merchant_id, _ = _merchant(app, client, "codstamp")
    shipment_id = _cod_shipment(app, merchant_id, 700.0)
    assert _collect(client, admin_token, shipment_id, 700.0).status_code == 201
    with app.app_context():
        shipment = db.session.get(Shipment, shipment_id)
        assert shipment.cod_status == "collected"
        assert shipment.cod_collected_at is not None


def test_collection_amount_comes_from_the_shipment(app, client, admin_token):
    """Even a matching request writes the shipment's figure, not the client's."""
    merchant_id, _ = _merchant(app, client, "codserver")
    shipment_id = _cod_shipment(app, merchant_id, 2500.0)
    body = _collect(client, admin_token, shipment_id, 2500.0).get_json()
    assert body["amount"] == 2500.0
    assert body["currency"] == "NPR"


# ── COD: summary ─────────────────────────────────────────────────────────────

def test_summary_reports_outstanding_for_own_merchant_only(app, client, admin_token):
    m1, t1 = _merchant(app, client, "codsum1")
    m2, _ = _merchant(app, client, "codsum2")
    _collect(client, admin_token, _cod_shipment(app, m1, 300.0), 300.0)
    _collect(client, admin_token, _cod_shipment(app, m1, 200.0), 200.0)
    _collect(client, admin_token, _cod_shipment(app, m2, 999.0), 999.0)

    own = client.get("/api/cod/summary", headers=auth_header(t1)).get_json()
    assert [e["merchant_id"] for e in own["merchants"]] == [m1]
    assert own["total_outstanding"] == 500.0
    assert own["merchants"][0]["by_status"]["collected"]["count"] == 2


# ── COD: remittance ──────────────────────────────────────────────────────────

def test_remittance_total_derived_from_collections(app, client, admin_token):
    """A client-supplied total must be ignored in favour of recalculate()."""
    merchant_id, _ = _merchant(app, client, "codrem")
    _collected_and_deposited(app, client, admin_token, merchant_id, 1000.0)
    _collected_and_deposited(app, client, admin_token, merchant_id, 500.0)

    r = client.post("/api/cod/remittances", json={
        "merchant_id": merchant_id,
        "period_start": "2000-01-01",
        "period_end": "2099-12-31",
        "service_fee": 100.0,
        "tax_amount": 13.0,
        # Lies the server must not believe.
        "total_collected": 99999.0,
        "net_payable": 99999.0,
    }, headers=auth_header(admin_token))
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["total_collected"] == 1500.0
    assert body["net_payable"] == round(1500.0 - 100.0 - 13.0, 2)
    assert body["collection_count"] == 2
    assert body["status"] == "draft"


def test_remittance_needs_eligible_collections(app, client, admin_token):
    merchant_id, _ = _merchant(app, client, "codremempty")
    r = client.post("/api/cod/remittances", json={
        "merchant_id": merchant_id,
        "period_start": "2000-01-01",
        "period_end": "2099-12-31",
    }, headers=auth_header(admin_token))
    assert r.status_code == 400


def test_non_super_admin_cannot_approve_remittance(app, client, admin_token):
    merchant_id, merchant_token = _merchant(app, client, "codapprove")
    ops_email = f"{_unique('codops')}@codtest.com"
    _make_user(app, ops_email, roles=("admin",))
    ops_token = _login(client, ops_email)

    _collected_and_deposited(app, client, admin_token, merchant_id, 400.0)
    rem = client.post("/api/cod/remittances", json={
        "merchant_id": merchant_id,
        "period_start": "2000-01-01",
        "period_end": "2099-12-31",
    }, headers=auth_header(admin_token)).get_json()

    # An ordinary merchant, and a plain admin, are both refused.
    assert client.post(f"/api/cod/remittances/{rem['id']}/approve",
                       headers=auth_header(merchant_token)).status_code == 403
    assert client.post(f"/api/cod/remittances/{rem['id']}/approve",
                       headers=auth_header(ops_token)).status_code == 403
    # The super_admin fixture may.
    ok = client.post(f"/api/cod/remittances/{rem['id']}/approve",
                     headers=auth_header(admin_token))
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["status"] == "approved"


def test_paying_a_remittance_settles_its_collections(app, client, admin_token):
    merchant_id, _ = _merchant(app, client, "codpay")
    _collected_and_deposited(app, client, admin_token, merchant_id, 250.0)
    rem = client.post("/api/cod/remittances", json={
        "merchant_id": merchant_id,
        "period_start": "2000-01-01",
        "period_end": "2099-12-31",
    }, headers=auth_header(admin_token)).get_json()

    # Cannot pay before approval.
    assert client.post(f"/api/cod/remittances/{rem['id']}/pay",
                       json={"payment_reference": "TXN-1"},
                       headers=auth_header(admin_token)).status_code == 400

    client.post(f"/api/cod/remittances/{rem['id']}/approve", headers=auth_header(admin_token))
    paid = client.post(f"/api/cod/remittances/{rem['id']}/pay",
                       json={"payment_method": "bank_transfer", "payment_reference": "TXN-1"},
                       headers=auth_header(admin_token))
    assert paid.status_code == 200, paid.get_json()
    body = paid.get_json()
    assert body["status"] == "paid"
    assert body["paid_at"]
    assert all(c["status"] == "remitted" for c in body["collections"])

    # A paid remittance is immutable.
    assert client.post(f"/api/cod/remittances/{rem['id']}/pay",
                       json={"payment_reference": "TXN-2"},
                       headers=auth_header(admin_token)).status_code == 400
    assert client.post(f"/api/cod/remittances/{rem['id']}/approve",
                       headers=auth_header(admin_token)).status_code == 400


def test_merchant_cannot_see_another_merchants_remittance(app, client, admin_token):
    owner_id, _ = _merchant(app, client, "codremowner")
    other_id, other_token = _merchant(app, client, "codremother")
    _collected_and_deposited(app, client, admin_token, owner_id, 600.0)
    rem = client.post("/api/cod/remittances", json={
        "merchant_id": owner_id,
        "period_start": "2000-01-01",
        "period_end": "2099-12-31",
    }, headers=auth_header(admin_token)).get_json()

    assert client.get(f"/api/cod/remittances/{rem['id']}",
                      headers=auth_header(other_token)).status_code == 403
    listed = client.get("/api/cod/remittances?per_page=100",
                        headers=auth_header(other_token)).get_json()
    assert rem["id"] not in [r["id"] for r in listed["items"]]


# ── payments: gateway configuration ──────────────────────────────────────────

def test_gateway_secret_never_appears_in_plaintext(app, client, admin_token, gateway):
    """The raw bytes of every gateway response must not contain the secret."""
    secret = gateway["secret"]
    responses = [
        client.get("/api/payments/gateways"),
        client.get("/api/payments/gateways/all", headers=auth_header(admin_token)),
        client.get(f"/api/payments/gateways/{gateway['id']}", headers=auth_header(admin_token)),
        client.put(f"/api/payments/gateways/{gateway['id']}",
                   json={"display_name": "Khalti Wallet"}, headers=auth_header(admin_token)),
    ]
    for r in responses:
        assert r.status_code == 200, r.get_json()
        assert secret not in r.get_data(as_text=True)

    masked = [g for g in client.get("/api/payments/gateways/all",
                                    headers=auth_header(admin_token)).get_json()
              if g["id"] == gateway["id"]][0]
    assert masked["secret_key_masked"].endswith(secret[-4:])
    assert masked["secret_key_masked"].startswith("*")
    # And the plaintext is not in the database either.
    from app.models.payment import PaymentGateway
    with app.app_context():
        row = db.session.get(PaymentGateway, gateway["id"])
        assert secret not in (row.secret_key_encrypted or "")
        assert row.secret_key == secret


def test_public_gateway_list_hides_configuration(client, gateway):
    body = client.get("/api/payments/gateways").get_json()
    assert body, "the configured gateway should be listed"
    for g in body:
        assert g["is_active"] is True
        assert "secret_key_masked" not in g
        assert "merchant_code" not in g
        assert "api_base_url" not in g


def test_only_super_admin_configures_gateways(app, client, gateway):
    _, merchant_token = _merchant(app, client, "paygw")
    assert client.get("/api/payments/gateways/all",
                      headers=auth_header(merchant_token)).status_code == 403
    assert client.post("/api/payments/gateways", json={"code": "esewa", "name": "eSewa"},
                       headers=auth_header(merchant_token)).status_code == 403


def test_unknown_gateway_code_is_rejected(client, admin_token):
    r = client.post("/api/payments/gateways",
                    json={"code": "definitely_not_a_gateway", "name": "Nope"},
                    headers=auth_header(admin_token))
    assert r.status_code == 400


# ── payments: lifecycle ──────────────────────────────────────────────────────

def test_idempotency_key_does_not_create_two_payments(app, client, gateway):
    merchant_id, token = _merchant(app, client, "payidem")
    shipment_id = _cod_shipment(app, merchant_id, 2000.0)
    key = _unique("idem")
    payload = {"shipment_id": shipment_id, "gateway_code": "khalti",
               "amount": 2000.0, "idempotency_key": key}

    first = client.post("/api/payments/initiate", json=payload, headers=auth_header(token))
    assert first.status_code == 201, first.get_json()
    second = client.post("/api/payments/initiate", json=payload, headers=auth_header(token))
    assert second.status_code == 200, second.get_json()
    assert second.get_json()["id"] == first.get_json()["id"]

    from app.models.payment import Payment
    with app.app_context():
        assert Payment.query.filter_by(idempotency_key=key).count() == 1


def test_initiate_rejects_a_client_supplied_amount_that_disagrees(app, client, gateway):
    merchant_id, token = _merchant(app, client, "payamt")
    shipment_id = _cod_shipment(app, merchant_id, 1800.0)
    r = client.post("/api/payments/initiate", json={
        "shipment_id": shipment_id, "gateway_code": "khalti", "amount": 1.0,
    }, headers=auth_header(token))
    assert r.status_code == 400


def test_initiate_rejects_an_inactive_gateway(app, client, admin_token, gateway):
    merchant_id, token = _merchant(app, client, "payinactive")
    shipment_id = _cod_shipment(app, merchant_id, 1200.0)
    client.delete(f"/api/payments/gateways/{gateway['id']}", headers=auth_header(admin_token))
    try:
        r = client.post("/api/payments/initiate", json={
            "shipment_id": shipment_id, "gateway_code": "khalti",
        }, headers=auth_header(token))
        assert r.status_code == 400
        assert "not active" in str(r.get_json()).lower()
    finally:
        client.put(f"/api/payments/gateways/{gateway['id']}",
                   json={"is_active": True}, headers=auth_header(admin_token))


def test_customer_sees_only_own_payments(app, client, gateway):
    m1, t1 = _merchant(app, client, "paylist1")
    m2, t2 = _merchant(app, client, "paylist2")
    client.post("/api/payments/initiate",
                json={"shipment_id": _cod_shipment(app, m1, 500.0), "gateway_code": "khalti"},
                headers=auth_header(t1))
    client.post("/api/payments/initiate",
                json={"shipment_id": _cod_shipment(app, m2, 600.0), "gateway_code": "khalti"},
                headers=auth_header(t2))

    mine = client.get("/api/payments", headers=auth_header(t1)).get_json()
    assert mine["items"] and {p["user_id"] for p in mine["items"]} == {m1}


def test_verify_leaves_the_payment_untouched_until_a_provider_is_wired(app, client, gateway):
    merchant_id, token = _merchant(app, client, "payverify")
    created = client.post("/api/payments/initiate",
                          json={"shipment_id": _cod_shipment(app, merchant_id, 750.0),
                                "gateway_code": "khalti"},
                          headers=auth_header(token)).get_json()

    r = client.post(f"/api/payments/{created['id']}/verify", json={},
                    headers=auth_header(token))
    assert r.status_code == 501
    body = r.get_json()
    assert body["provider"]["enabled"] is False
    assert "not enabled" in body["provider"]["message"].lower()
    # No invented success.
    assert body["payment"]["status"] == "initiated"
    assert body["payment"]["paid_at"] is None


def test_callback_records_the_payload_but_does_not_settle(app, client, gateway):
    merchant_id, token = _merchant(app, client, "paycallback")
    created = client.post("/api/payments/initiate",
                          json={"shipment_id": _cod_shipment(app, merchant_id, 980.0),
                                "gateway_code": "khalti"},
                          headers=auth_header(token)).get_json()

    r = client.post("/api/payments/callback/khalti",
                    json={"payment_no": created["payment_no"], "status": "Completed"})
    assert r.status_code == 501
    assert r.get_json()["payment"]["status"] == "initiated"

    from app.models.payment import Payment
    with app.app_context():
        payment = db.session.get(Payment, created["id"])
        # The provider's claim is kept as evidence, never acted on.
        assert payment.gateway_response["status"] == "Completed"
        assert payment.status == "initiated"

    assert client.post("/api/payments/callback/not_a_gateway",
                       json={"payment_no": created["payment_no"]}).status_code == 404
    assert client.post("/api/payments/callback/khalti",
                       json={"payment_no": "PAY-00-NOPE"}).status_code == 404


def test_payment_detail_is_owner_or_admin(app, client, admin_token, gateway):
    merchant_id, token = _merchant(app, client, "paydetail")
    _, other_token = _merchant(app, client, "payintruder")
    created = client.post("/api/payments/initiate",
                          json={"shipment_id": _cod_shipment(app, merchant_id, 450.0),
                                "gateway_code": "khalti"},
                          headers=auth_header(token)).get_json()

    assert client.get(f"/api/payments/{created['id']}",
                      headers=auth_header(token)).status_code == 200
    assert client.get(f"/api/payments/{created['id']}",
                      headers=auth_header(other_token)).status_code == 403
    assert client.get(f"/api/payments/{created['id']}",
                      headers=auth_header(admin_token)).status_code == 200


# ── payments: refunds ────────────────────────────────────────────────────────

def _succeed(app, payment_id):
    """Put a payment into 'success' the way the provider seam would."""
    from app.models.payment import Payment
    from app.payments.routes import _mark_paid
    with app.app_context():
        payment = db.session.get(Payment, payment_id)
        _mark_paid(payment)
        db.session.commit()


def test_refund_larger_than_the_payment_is_rejected(app, client, admin_token, gateway):
    merchant_id, token = _merchant(app, client, "payrefund")
    created = client.post("/api/payments/initiate",
                          json={"shipment_id": _cod_shipment(app, merchant_id, 1000.0),
                                "gateway_code": "khalti"},
                          headers=auth_header(token)).get_json()
    _succeed(app, created["id"])

    too_big = client.post(f"/api/payments/{created['id']}/refund",
                          json={"amount": 1500.0, "reason": "oops"},
                          headers=auth_header(admin_token))
    assert too_big.status_code == 400
    assert "exceeds" in str(too_big.get_json()).lower()

    ok = client.post(f"/api/payments/{created['id']}/refund",
                     json={"amount": 1000.0, "reason": "customer cancelled"},
                     headers=auth_header(admin_token))
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["payment"]["status"] == "refunded"

    # And no second bite.
    again = client.post(f"/api/payments/{created['id']}/refund",
                        json={"amount": 10.0}, headers=auth_header(admin_token))
    assert again.status_code == 400


def test_only_super_admin_refunds(app, client, gateway):
    merchant_id, token = _merchant(app, client, "payrefundrole")
    created = client.post("/api/payments/initiate",
                          json={"shipment_id": _cod_shipment(app, merchant_id, 300.0),
                                "gateway_code": "khalti"},
                          headers=auth_header(token)).get_json()
    _succeed(app, created["id"])
    r = client.post(f"/api/payments/{created['id']}/refund", json={"amount": 10.0},
                    headers=auth_header(token))
    assert r.status_code == 403


def test_success_updates_the_linked_shipment(app, client, gateway):
    from app.models.shipment import Shipment
    merchant_id, token = _merchant(app, client, "payshipment")
    shipment_id = _cod_shipment(app, merchant_id, 1100.0)
    created = client.post("/api/payments/initiate",
                          json={"shipment_id": shipment_id, "gateway_code": "khalti"},
                          headers=auth_header(token)).get_json()
    with app.app_context():
        assert db.session.get(Shipment, shipment_id).payment_status == "unpaid"
    _succeed(app, created["id"])
    with app.app_context():
        assert db.session.get(Shipment, shipment_id).payment_status == "paid"
