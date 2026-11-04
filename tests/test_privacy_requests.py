"""Data subject requests: the deadline, the gate, and what survives erasure.

Missing the one-month deadline in Art. 12(3) is itself the infringement, so
the clock and the overdue count are tested as behaviour rather than as
bookkeeping. The rest of the file is about the two ways this module could
leak: handing a file to the wrong person (identity verification, and the
deliberately thin public status lookup) and handing it to the right person
in the wrong place (the admin export writes a file and returns a path).

Erasure is tested for what it *keeps* as much as for what it removes. A
courier that deletes a carriage record or a tax invoice on request has
swapped one breach for another.
"""

import uuid
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.claim import AddressBookEntry
from app.models.marketing import MarketingContact
from app.models.privacy import DEFAULT_RETENTION, DataRequest, RetentionRule
from app.models.role import Role
from app.models.shipment import Shipment
from app.models.user import User, UserStatus
from app.models.user_session import UserSession
from tests.conftest import auth_header, register_and_login


# ── Fixtures and helpers ─────────────────────────────────────────────────────

@pytest.fixture
def admin(admin_token):
    """The conftest fixture account, which holds ``super_admin``."""
    return auth_header(admin_token)


@pytest.fixture
def public(app):
    return app.test_client()


@pytest.fixture
def plain_admin(app, client):
    """An ``admin`` who is not a ``super_admin``.

    Needed because erasure is the one action in the module that an ordinary
    administrator must not be able to take alone.
    """
    email = f"ops-{uuid.uuid4().hex[:8]}@example.test"
    password = "Admin1234!"
    user = User(username=f"ops{uuid.uuid4().hex[:8]}", email=email,
                first_name="Ops", last_name="Admin", status=UserStatus.ACTIVE)
    user.set_password(password)
    user.roles.append(Role.query.filter_by(name="admin").one())
    db.session.add(user)
    db.session.commit()
    result = client.post("/api/auth/login", json={"email": email, "password": password})
    assert result.status_code == 200, result.get_json()
    return auth_header(result.get_json()["access_token"])


@pytest.fixture
def export_dir(app, tmp_path):
    """Write admin bundles somewhere disposable, not into the repository."""
    previous = app.config.get("PRIVACY_EXPORT_DIR")
    app.config["PRIVACY_EXPORT_DIR"] = str(tmp_path / "privacy_exports")
    yield app.config["PRIVACY_EXPORT_DIR"]
    app.config["PRIVACY_EXPORT_DIR"] = previous


def _address(prefix="dsar"):
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.test"


def _file_request(client, address=None, kind=DataRequest.ACCESS, details="Please send my file"):
    address = address or _address()
    result = client.post("/api/privacy/requests",
                         json={"kind": kind, "email": address, "details": details})
    assert result.status_code == 201, result.get_json()
    reference = result.get_json()["reference"]
    return DataRequest.query.filter_by(reference=reference).one()


def _customer_with_history(client):
    """A signed-in customer with a shipment, a saved address and a session."""
    email = _address("cust")
    token = register_and_login(client, email)
    user = User.query.filter_by(email=email).one()
    booked = client.post("/api/shipments/", headers=auth_header(token), json={
        "origin": "Kathmandu", "destination": "London",
        "receiver_name": "Bimala Thapa",
        "receiver_email": "bimala@elsewhere.test",
        "receiver_phone": "+44 7700 900000",
        "receiver_address": "14 Mornington Crescent, London",
        "total_packages": 1,
    })
    assert booked.status_code == 201, booked.get_json()
    db.session.add(AddressBookEntry(user_id=user.id, label="Home",
                                    contact_name="Ram Bahadur", city="Kathmandu"))
    db.session.commit()
    return token, user


# ── Intake and the clock ─────────────────────────────────────────────────────

def test_filing_a_request_mints_a_reference_and_starts_the_month(public):
    """The deadline runs from arrival, not from when somebody picks it up."""
    address = _address()
    before = datetime.utcnow()
    result = public.post("/api/privacy/requests", json={
        "kind": DataRequest.ERASURE, "email": address,
        "details": "Delete my account please",
    })

    assert result.status_code == 201
    body = result.get_json()
    assert set(body) == {"reference", "message"}, "only the reference comes back"
    assert body["reference"].startswith("DSR-")

    record = DataRequest.query.filter_by(reference=body["reference"]).one()
    assert record.status == DataRequest.RECEIVED
    assert record.identity_verified_at is None
    assert record.due_at is not None
    expected = before + timedelta(days=DataRequest.RESPONSE_DAYS)
    assert abs((record.due_at - expected).total_seconds()) < 60
    assert record.deadline == record.due_at
    assert record.is_overdue is False


def test_a_request_needs_a_recognised_kind(public):
    result = public.post("/api/privacy/requests",
                         json={"kind": "everything", "email": _address()})
    assert result.status_code == 400


def test_an_account_holder_is_linked_but_not_thereby_verified(public, client):
    """Matching an email is a convenience for the queue, never proof of identity."""
    email = _address("known")
    register_and_login(client, email)
    record = _file_request(public, email)
    assert record.user_id == User.query.filter_by(email=email).one().id
    assert record.identity_verified_at is None


# ── The public status lookup leaks nothing ───────────────────────────────────

def test_the_reference_lookup_returns_only_a_coarse_status(public, admin):
    """A reference travels in email and screenshots. It is not a credential."""
    address = _address("leak")
    record = _file_request(public, address, details="Top secret grievance")
    public.post(f"/api/privacy/requests/{record.id}/verify", headers=admin,
                json={"note": "Passport seen in branch"})

    result = public.get(f"/api/privacy/requests/{record.reference}")
    body = result.get_json()
    assert result.status_code == 200
    assert set(body) == {"reference", "status"}
    serialised = str(body)
    for secret in (address, "Top secret grievance", "Passport seen in branch"):
        assert secret not in serialised


def test_an_unknown_reference_is_a_plain_404(public):
    assert public.get("/api/privacy/requests/DSR-0000-ABCDEF").status_code == 404


# ── Self-service export ──────────────────────────────────────────────────────

def test_me_export_returns_the_signed_in_user_s_own_bundle(client):
    token, user = _customer_with_history(client)
    result = client.get("/api/privacy/me/export", headers=auth_header(token))
    bundle = result.get_json()

    assert result.status_code == 200
    assert set(bundle) >= {"profile", "shipments", "addresses", "invoices",
                           "consent_records", "marketing", "sessions"}
    assert bundle["profile"]["email"] == user.email
    assert len(bundle["shipments"]) == 1
    assert len(bundle["addresses"]) == 1
    assert all(s["user_id"] == user.id for s in bundle["shipments"])


def test_me_export_withholds_the_recipient_s_contact_details(client):
    """Art. 15(4): the copy must not adversely affect the rights of others."""
    token, _ = _customer_with_history(client)
    bundle = client.get("/api/privacy/me/export",
                        headers=auth_header(token)).get_json()
    shipment = bundle["shipments"][0]

    assert shipment["receiver_name"] == "Bimala Thapa", \
        "the subject has to be able to recognise their own shipment"
    assert shipment["destination"] == "London"
    for withheld in ("receiver_email", "receiver_phone", "receiver_address"):
        assert shipment[withheld] is None
    assert shipment["_recipient_contact_withheld"] is True
    assert "bimala@elsewhere.test" not in str(bundle)


def test_me_export_needs_a_session(public):
    assert public.get("/api/privacy/me/export").status_code in (401, 422)


# ── Nothing moves before identity is established ─────────────────────────────

def test_export_is_refused_before_identity_is_verified(public, admin, export_dir):
    record = _file_request(public)
    result = public.post(f"/api/privacy/requests/{record.id}/export", headers=admin)
    assert result.status_code == 409
    assert "verif" in result.get_json()["message"].lower()
    assert db.session.get(DataRequest, record.id).export_path is None


def test_erase_is_refused_before_identity_is_verified(public, admin):
    record = _file_request(public, kind=DataRequest.ERASURE)
    result = public.post(f"/api/privacy/requests/{record.id}/erase", headers=admin,
                         json={"confirm": record.reference, "note": "asked for it"})
    assert result.status_code == 409


def test_verifying_records_how_and_moves_the_request_on(public, admin):
    record = _file_request(public)
    blank = public.post(f"/api/privacy/requests/{record.id}/verify", headers=admin,
                        json={"note": "   "})
    assert blank.status_code == 400, "'verified' with no evidence is not evidence"

    result = public.post(f"/api/privacy/requests/{record.id}/verify", headers=admin,
                         json={"note": "Citizenship certificate seen at Kalanki branch"})
    body = result.get_json()
    assert result.status_code == 200
    assert body["identity_verified"] is True
    assert body["status"] == DataRequest.IN_PROGRESS


def test_the_admin_export_writes_a_file_and_returns_only_its_path(public, client,
                                                                  admin, export_dir):
    """A complete personal file does not belong in an API response or an access log."""
    import json
    import os

    _, user = _customer_with_history(client)
    record = _file_request(public, user.email)
    public.post(f"/api/privacy/requests/{record.id}/verify", headers=admin,
                json={"note": "Verified by callback to the number on file"})

    result = public.post(f"/api/privacy/requests/{record.id}/export", headers=admin)
    body = result.get_json()
    assert result.status_code == 201
    assert set(body) == {"reference", "export_path", "sections", "note"}
    # The response names the sections; it does not carry any of their contents.
    assert "profile" in body["sections"]
    assert user.email not in str(body)

    path = body["export_path"]
    assert os.path.isfile(path)
    assert os.path.dirname(path) == os.path.abspath(export_dir)
    with open(path, encoding="utf-8") as handle:
        bundle = json.load(handle)
    assert bundle["profile"]["email"] == user.email
    assert bundle["shipments"][0]["receiver_phone"] is None
    assert db.session.get(DataRequest, record.id).export_path == path


# ── Extension, completion, refusal ───────────────────────────────────────────

def test_the_extension_needs_a_reason_and_adds_two_months(public, admin):
    record = _file_request(public)
    due = record.due_at

    refused = public.post(f"/api/privacy/requests/{record.id}/extend", headers=admin,
                          json={"reason": ""})
    assert refused.status_code == 400

    result = public.post(f"/api/privacy/requests/{record.id}/extend", headers=admin,
                         json={"reason": "Four years of archived shipments to collate"})
    assert result.status_code == 200
    record = db.session.get(DataRequest, record.id)
    assert record.extended_until == due + timedelta(days=60)
    assert record.deadline == record.extended_until

    twice = public.post(f"/api/privacy/requests/{record.id}/extend", headers=admin,
                        json={"reason": "again"})
    assert twice.status_code == 409, "Art. 12(3) allows two further months, once"


def test_completing_and_refusing_both_demand_a_note(public, admin):
    done = _file_request(public)
    assert public.post(f"/api/privacy/requests/{done.id}/complete", headers=admin,
                       json={}).status_code == 400
    result = public.post(f"/api/privacy/requests/{done.id}/complete", headers=admin,
                         json={"note": "Bundle emailed to the verified address"})
    assert result.status_code == 200
    assert result.get_json()["status"] == DataRequest.COMPLETED

    refused = _file_request(public)
    assert public.post(f"/api/privacy/requests/{refused.id}/refuse", headers=admin,
                       json={"note": ""}).status_code == 400
    result = public.post(f"/api/privacy/requests/{refused.id}/refuse", headers=admin,
                         json={"note": "Manifestly unfounded: eleventh identical "
                                       "request this month, Art. 12(5)(b)"})
    assert result.status_code == 200
    assert result.get_json()["status"] == DataRequest.REFUSED


# ── Erasure ──────────────────────────────────────────────────────────────────

def test_erase_is_closed_to_an_ordinary_admin(public, plain_admin, admin):
    record = _file_request(public, kind=DataRequest.ERASURE)
    public.post(f"/api/privacy/requests/{record.id}/verify", headers=admin,
                json={"note": "Verified in branch"})
    result = public.post(f"/api/privacy/requests/{record.id}/erase",
                         headers=plain_admin,
                         json={"confirm": record.reference, "note": "go"})
    assert result.status_code == 403


def test_erase_needs_the_reference_typed_back(public, admin):
    record = _file_request(public, kind=DataRequest.ERASURE)
    public.post(f"/api/privacy/requests/{record.id}/verify", headers=admin,
                json={"note": "Verified in branch"})

    for body in ({"note": "go"}, {"confirm": True, "note": "go"},
                 {"confirm": "yes", "note": "go"}):
        result = public.post(f"/api/privacy/requests/{record.id}/erase",
                             headers=admin, json=body)
        assert result.status_code == 400, body
    # ... and with a note missing, even the right confirmation is not enough.
    assert public.post(f"/api/privacy/requests/{record.id}/erase", headers=admin,
                       json={"confirm": record.reference}).status_code == 400


def test_erasure_anonymises_what_has_to_survive_and_deletes_what_does_not(
        public, client, admin):
    token, user = _customer_with_history(client)
    user_id = user.id
    original_email = user.email
    shipment_id = Shipment.query.filter_by(user_id=user_id).one().id
    contact = MarketingContact(email=original_email, name="Ram Bahadur",
                               status=MarketingContact.SUBSCRIBED,
                               confirmed_at=datetime.utcnow(), tags=["diaspora-uk"],
                               consent_text="Tick to receive updates",
                               unsubscribe_token=MarketingContact.new_token())
    db.session.add(contact)
    db.session.commit()
    assert UserSession.query.filter_by(user_id=user_id).count() >= 1

    record = _file_request(public, original_email, kind=DataRequest.ERASURE)
    public.post(f"/api/privacy/requests/{record.id}/verify", headers=admin,
                json={"note": "Passport and a signed letter"})
    result = public.post(f"/api/privacy/requests/{record.id}/erase", headers=admin,
                         json={"confirm": record.reference,
                               "note": "Art. 17 request, no outstanding claims"})

    assert result.status_code == 200
    report = result.get_json()["report"]

    # Deleted: nothing depends on these.
    assert report["deleted"]["user_sessions"] >= 1
    assert report["deleted"]["address_book_entries"] == 1
    assert UserSession.query.filter_by(user_id=user_id).count() == 0
    assert AddressBookEntry.query.filter_by(user_id=user_id).count() == 0

    # Anonymised: the row survives because other records point at it.
    erased = db.session.get(User, user_id)
    assert erased is not None
    assert erased.email == f"erased-{user_id}@erased.invalid"
    assert erased.first_name == "Erased"
    assert erased.status == UserStatus.INACTIVE

    shipment = db.session.get(Shipment, shipment_id)
    assert shipment is not None, "the carriage record has to survive"
    assert shipment.sender_name == "[erased]"
    assert shipment.sender_email is None
    assert shipment.receiver_name == "Bimala Thapa", \
        "the recipient is a third party; this erasure does not reach them"

    # Suppression survives, stripped to the minimum that honours the objection.
    suppression = MarketingContact.query.filter_by(email=original_email).one()
    assert suppression.status == MarketingContact.UNSUBSCRIBED
    assert suppression.name is None
    assert suppression.tags == []
    assert suppression.consent_text is None
    assert report["retained"]["marketing_suppression"] == 1

    assert db.session.get(DataRequest, record.id).status == DataRequest.COMPLETED
    assert "invoices" in report["retained"]
    assert any("[TO CONFIRM" in note for note in report["notes"])


# ── The queue and the overdue count ──────────────────────────────────────────

def test_the_queue_puts_the_nearest_deadline_first(public, admin):
    far = _file_request(public)
    near = _file_request(public)
    near.due_at = datetime.utcnow() + timedelta(days=1)
    db.session.commit()

    body = public.get("/api/privacy/queue?per_page=100", headers=admin).get_json()
    assert set(body) >= {"items", "total", "page", "per_page", "pages"}
    references = [item["reference"] for item in body["items"]]
    assert references.index(near.reference) < references.index(far.reference)


def test_the_queue_filters_by_kind_and_overdue(public, admin):
    record = _file_request(public, kind=DataRequest.PORTABILITY)
    record.due_at = datetime.utcnow() - timedelta(days=3)
    db.session.commit()

    overdue = public.get("/api/privacy/queue?overdue=true&per_page=100",
                         headers=admin).get_json()
    assert record.reference in [i["reference"] for i in overdue["items"]]
    assert all(i["is_overdue"] for i in overdue["items"])

    not_overdue = public.get("/api/privacy/queue?overdue=false&per_page=100",
                             headers=admin).get_json()
    assert record.reference not in [i["reference"] for i in not_overdue["items"]]

    by_kind = public.get(
        f"/api/privacy/queue?kind={DataRequest.PORTABILITY}&per_page=100",
        headers=admin).get_json()
    assert all(i["kind"] == DataRequest.PORTABILITY for i in by_kind["items"])


def test_the_overdue_count_is_the_number_that_matters(public, admin):
    """Late is the breach, so this is a count, not a report."""
    before = public.get("/api/privacy/overdue", headers=admin).get_json()["overdue"]

    late = _file_request(public)
    late.due_at = datetime.utcnow() - timedelta(days=5)
    soon = _file_request(public)
    soon.due_at = datetime.utcnow() + timedelta(days=2)
    db.session.commit()

    body = public.get("/api/privacy/overdue", headers=admin).get_json()
    assert body["overdue"] == before + 1
    assert body["due_within_7_days"] >= 1

    # Closing it stops the clock: an answered request cannot still be late.
    public.post(f"/api/privacy/requests/{late.id}/complete", headers=admin,
                json={"note": "Answered, late, and recorded as such"})
    after = public.get("/api/privacy/overdue", headers=admin).get_json()["overdue"]
    assert after == before


def test_an_extension_takes_a_request_off_the_overdue_list(public, admin):
    record = _file_request(public)
    record.due_at = datetime.utcnow() - timedelta(days=1)
    db.session.commit()
    before = public.get("/api/privacy/overdue", headers=admin).get_json()["overdue"]

    public.post(f"/api/privacy/requests/{record.id}/extend", headers=admin,
                json={"reason": "Records held across three branches"})
    after = public.get("/api/privacy/overdue", headers=admin).get_json()["overdue"]
    assert after == before - 1


# ── Retention ────────────────────────────────────────────────────────────────

def test_seeding_retention_is_idempotent_and_never_purges(public, admin):
    first = public.post("/api/privacy/retention/seed", headers=admin)
    assert first.status_code == 200
    assert first.get_json()["total"] == len(DEFAULT_RETENTION)

    again = public.post("/api/privacy/retention/seed", headers=admin).get_json()
    assert again["created"] == 0, "re-seeding must not duplicate the schedule"

    listed = public.get("/api/privacy/retention", headers=admin).get_json()
    codes = {item["code"] for item in listed["items"]}
    assert codes >= {entry[0] for entry in DEFAULT_RETENTION}
    assert all(item["auto_purge"] is False for item in listed["items"])
    assert listed["unconfirmed"] >= 1, "the [TO CONFIRM] markers are the point"


def test_a_retention_period_can_be_changed_but_its_target_cannot(public, admin):
    public.post("/api/privacy/retention/seed", headers=admin)
    rule = RetentionRule.query.filter_by(code="audit_logs").one()

    blocked = public.put(f"/api/privacy/retention/{rule.id}", headers=admin,
                         json={"target": "Shipment"})
    assert blocked.status_code == 403

    result = public.put(f"/api/privacy/retention/{rule.id}", headers=admin, json={
        "retain_days": 730,
        "basis": "Confirmed with counsel 2026-11: two years for security forensics.",
    })
    assert result.status_code == 200
    assert result.get_json()["retain_days"] == 730

    # A re-seed must not quietly undo a period somebody lengthened on advice.
    public.post("/api/privacy/retention/seed", headers=admin)
    assert RetentionRule.query.filter_by(code="audit_logs").one().retain_days == 730

    assert public.put(f"/api/privacy/retention/{rule.id}", headers=admin,
                      json={"retain_days": 0}).status_code == 400


def test_privacy_admin_endpoints_reject_an_ordinary_visitor(public):
    for path in ("/api/privacy/queue", "/api/privacy/overdue", "/api/privacy/retention"):
        assert public.get(path).status_code in (401, 422), path
