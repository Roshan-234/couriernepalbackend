"""Consent: the evidence, not just the behaviour.

The old banner stored a choice in `localStorage`. That honours a decision but
cannot demonstrate one, which is what GDPR Art. 7(1) actually asks for. These
tests pin the properties that make the new path evidence: a decision is a row,
refusing is recorded as explicitly as accepting, the two cost the same, and the
server — not the browser — is the source of truth.

Every test takes its own `test_client`, because the shared session-scoped
client carries one cookie jar and a visitor identity is exactly what this
module is about.
"""

import pytest

from app.extensions import db
from app.models.consent import (
    ANALYTICS,
    CURRENT_POLICY_VERSION,
    MARKETING,
    NECESSARY,
    OPTIONAL_CATEGORIES,
    ConsentRecord,
    hash_identifier,
)


@pytest.fixture
def visitor(app):
    """A browser that has never been to the site."""
    return app.test_client()


# ── The decision is a row, and refusal is recorded ───────────────────────────

def test_fresh_visitor_has_no_cookie_and_no_consent(visitor):
    """Nothing is stored in a browser before a choice is made.

    This is the ePrivacy line: the identifier that remembers the decision is
    strictly necessary only once there *is* a decision to remember.
    """
    r = visitor.get("/api/consent/")
    body = r.get_json()
    assert r.status_code == 200
    assert body["state"] == "unknown"
    assert body["needs_decision"] is True
    assert body["allows"] == {NECESSARY: True, ANALYTICS: False,
                              MARKETING: False, "preferences": False}
    assert "mf_vid" not in r.headers.get("Set-Cookie", "")


def test_refusing_everything_writes_a_record(visitor, app):
    """A refusal is evidence. An absent record is only an absence."""
    r = visitor.post("/api/consent/", json={"categories": []})
    assert r.status_code == 201
    assert r.get_json()["categories"] == [NECESSARY]

    with app.app_context():
        record = (ConsentRecord.query
                  .order_by(ConsentRecord.id.desc()).first())
        assert record.action == ConsentRecord.GIVEN
        assert record.categories == [NECESSARY]
        assert record.policy_version == CURRENT_POLICY_VERSION
        assert record.expires_at is not None


def test_accepting_and_refusing_are_the_same_shape(app):
    """Recital 42: refusing must not be the harder path, starting at the API.

    Same status, same keys, same single request. If a future change makes
    one of these cheaper than the other, this fails.
    """
    accept = app.test_client().post("/api/consent/",
                                    json={"categories": list(OPTIONAL_CATEGORIES)})
    refuse = app.test_client().post("/api/consent/", json={"categories": []})

    assert accept.status_code == refuse.status_code == 201
    assert set(accept.get_json()) == set(refuse.get_json())
    assert (("mf_vid" in accept.headers.get("Set-Cookie", ""))
            is ("mf_vid" in refuse.headers.get("Set-Cookie", "")) is True)


def test_decision_is_read_back_from_the_server(visitor):
    """The browser copy is a cache; the row is the fact."""
    visitor.post("/api/consent/", json={"categories": [ANALYTICS]})
    state = visitor.get("/api/consent/").get_json()
    assert state["state"] == "decided"
    assert state["allows"][ANALYTICS] is True
    assert state["allows"][MARKETING] is False
    assert state["needs_decision"] is False


def test_changing_a_preference_appends_rather_than_edits(visitor, app):
    """History is the audit trail, so nothing is updated in place."""
    visitor.post("/api/consent/", json={"categories": []})
    with app.app_context():
        first = ConsentRecord.query.order_by(ConsentRecord.id.desc()).first()
        first_id, before = first.id, ConsentRecord.query.count()

    r = visitor.post("/api/consent/",
                     json={"categories": [ANALYTICS], "source": "preferences"})
    assert r.get_json()["action"] == ConsentRecord.UPDATED

    with app.app_context():
        assert ConsentRecord.query.count() == before + 1
        # The original decision is untouched.
        assert db.session.get(ConsentRecord, first_id).categories == [NECESSARY]


# ── Withdrawal ───────────────────────────────────────────────────────────────

def test_withdrawal_is_one_call_and_is_recorded(visitor, app):
    """Art. 7(3): as easy to take back as to give. No login, no second step."""
    visitor.post("/api/consent/", json={"categories": [ANALYTICS, MARKETING]})
    r = visitor.delete("/api/consent/")
    assert r.status_code == 200
    body = r.get_json()
    assert body["state"] == "withdrawn"
    assert body["allows"][ANALYTICS] is False
    assert body["allows"][MARKETING] is False
    assert body["allows"][NECESSARY] is True

    with app.app_context():
        latest = ConsentRecord.query.order_by(ConsentRecord.id.desc()).first()
        assert latest.action == ConsentRecord.WITHDRAWN


def test_withdrawing_without_a_prior_decision_reveals_nothing(visitor):
    """Answered identically whether or not there was anything to withdraw."""
    r = visitor.delete("/api/consent/")
    assert r.status_code == 200
    assert r.get_json()["needs_decision"] is True


# ── Input is not trusted ─────────────────────────────────────────────────────

def test_unknown_categories_are_refused(visitor):
    r = visitor.post("/api/consent/", json={"categories": ["sell-my-data"]})
    assert r.status_code == 400


def test_categories_must_be_a_list_of_strings(visitor):
    assert visitor.post("/api/consent/",
                        json={"categories": "analytics"}).status_code == 400
    assert visitor.post("/api/consent/",
                        json={"categories": [{"analytics": True}]}).status_code == 400


def test_necessary_cannot_be_refused_and_is_always_present(visitor):
    r = visitor.post("/api/consent/", json={"categories": [ANALYTICS]})
    assert NECESSARY in r.get_json()["categories"]


def test_a_forged_subject_key_cookie_is_ignored(app):
    """The cookie is a pointer, not an authority. Junk resolves to no consent."""
    client = app.test_client()
    client.set_cookie("mf_vid", "../../etc/passwd", domain="localhost")
    state = client.get("/api/consent/").get_json()
    assert state["needs_decision"] is True
    assert state["allows"][ANALYTICS] is False


def test_an_unknown_but_well_formed_key_grants_nothing(app):
    """Guessing a 24-byte key would have to also hit a real row to matter."""
    client = app.test_client()
    client.set_cookie("mf_vid", "Zm9ydGhpc2lzbm90YXJlYWxrZXlhdGFsbA", domain="localhost")
    assert client.get("/api/consent/").get_json()["allows"][ANALYTICS] is False


# ── What is kept as evidence, and what is not ────────────────────────────────

def test_the_raw_address_is_never_stored(visitor, app):
    """A hash can confirm 'same visitor'. An address identifies a person."""
    visitor.post("/api/consent/", json={"categories": [ANALYTICS]},
                 environ_base={"REMOTE_ADDR": "203.0.113.45"})
    with app.app_context():
        record = ConsentRecord.query.order_by(ConsentRecord.id.desc()).first()
        assert record.ip_hash
        assert "203.0.113.45" not in (record.ip_hash or "")
        columns = {c.name for c in ConsentRecord.__table__.columns}
        assert "ip" not in columns and "ip_address" not in columns


def test_the_ip_hash_is_salted(app):
    """An unsalted IPv4 hash is reversible by brute force in seconds."""
    import hashlib
    plain = hashlib.sha256(b"203.0.113.45").hexdigest()
    assert hash_identifier("203.0.113.45") != plain


def test_a_stale_policy_version_asks_again(visitor, app):
    """Consent to wording nobody has seen is not consent."""
    visitor.post("/api/consent/", json={"categories": [ANALYTICS]})
    with app.app_context():
        record = ConsentRecord.query.order_by(ConsentRecord.id.desc()).first()
        record.policy_version = "1999-01-01.0"
        db.session.commit()

    state = visitor.get("/api/consent/").get_json()
    assert state["state"] == "stale"
    assert state["needs_decision"] is True
    assert state["allows"][ANALYTICS] is False


def test_an_expired_decision_asks_again(visitor, app):
    from datetime import datetime, timedelta
    visitor.post("/api/consent/", json={"categories": [ANALYTICS]})
    with app.app_context():
        record = ConsentRecord.query.order_by(ConsentRecord.id.desc()).first()
        record.expires_at = datetime.utcnow() - timedelta(days=1)
        db.session.commit()
    assert visitor.get("/api/consent/").get_json()["needs_decision"] is True


# ── The published policy ─────────────────────────────────────────────────────

def test_policy_lists_every_category_with_wording(visitor):
    """The banner has to show the wording matching the version it submits."""
    body = visitor.get("/api/consent/policy").get_json()
    assert body["policy_version"] == CURRENT_POLICY_VERSION
    keys = {c["key"] for c in body["categories"]}
    assert keys == {NECESSARY, *OPTIONAL_CATEGORIES}
    for category in body["categories"]:
        assert category["label"] and len(category["description"]) > 30
    necessary = next(c for c in body["categories"] if c["key"] == NECESSARY)
    assert necessary["required"] is True
    assert all(c["required"] is False
               for c in body["categories"] if c["key"] != NECESSARY)


# ── Administration is not public ─────────────────────────────────────────────

def test_the_consent_log_requires_an_admin(visitor):
    assert visitor.get("/api/consent/records").status_code == 401
    assert visitor.get("/api/consent/summary").status_code == 401


def test_admin_can_read_the_log_with_evidence(client, admin_token, app):
    app.test_client().post("/api/consent/", json={"categories": [ANALYTICS]})
    r = client.get("/api/consent/records",
                   headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] >= 1
    row = body["items"][0]
    # Evidence enough to answer a regulator, and nothing reversible.
    assert {"subject_key", "ip_hash", "policy_version", "created_at"} <= set(row)


def test_summary_reports_acceptance_rates(client, admin_token, app):
    for categories in ([ANALYTICS], [], [ANALYTICS, MARKETING]):
        app.test_client().post("/api/consent/", json={"categories": categories})
    r = client.get("/api/consent/summary",
                   headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["policy_version"] == CURRENT_POLICY_VERSION
    assert body["decisions_in_force"] >= 3
    for category in OPTIONAL_CATEGORIES:
        assert 0.0 <= body["accepted"][category]["rate"] <= 100.0


def test_a_plain_customer_cannot_read_the_consent_log(client):
    from tests.conftest import _login, _register
    email = "consent_customer@test.com"
    _register(client, email)
    token = _login(client, email)
    r = client.get("/api/consent/records",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
