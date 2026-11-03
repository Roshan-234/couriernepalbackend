"""Behavioural coverage for the authentication and session hardening.

Each test here corresponds to a finding from the security audit. They assert
the observable behaviour an attacker would probe — the response an outsider
sees, and the state left in the database — rather than that a particular line
of code exists.
"""
import pytest

from app.extensions import db
from app.models.user import MAX_FAILED_LOGINS, User, UserStatus
from app.models.role import Role
from app.models.user_session import UserSession
from tests.conftest import auth_header, register_and_login

PASSWORD = "Admin1234!"


def _make_user(app, email, password=PASSWORD, role="customer"):
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user:
            return user.id
        base = email.split("@")[0]
        name, i = base, 1
        while User.query.filter_by(username=name).first():
            name, i = f"{base}{i}", i + 1
        user = User(username=name, email=email, first_name="Sec", last_name="Test",
                    status=UserStatus.ACTIVE)
        user.set_password(password)
        user.roles.append(Role.query.filter_by(name=role).one())
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# ── Account lockout and enumeration ─────────────────────────────────────────

def test_repeated_failures_lock_the_account(client, app):
    email = "lockout.target@example.test"
    _make_user(app, email)

    for _ in range(MAX_FAILED_LOGINS):
        assert _login(client, email, "WrongPass1!").status_code == 401

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        assert user.is_locked, "the account should be locked after repeated failures"

    # Even the right password is refused while the lock holds.
    assert _login(client, email).status_code == 401


def test_a_locked_account_is_indistinguishable_from_a_wrong_password(client, app):
    """Saying 'account locked' would confirm the address exists."""
    email = "lockout.quiet@example.test"
    _make_user(app, email)
    for _ in range(MAX_FAILED_LOGINS):
        _login(client, email, "WrongPass1!")

    locked = _login(client, email)
    unknown = _login(client, "definitely.not.registered@example.test")
    assert locked.status_code == unknown.status_code == 401
    assert locked.get_json() == unknown.get_json(), "the two answers differ"


def test_unknown_and_wrong_password_give_the_same_answer(client, app):
    _make_user(app, "enum.known@example.test")
    wrong = _login(client, "enum.known@example.test", "WrongPass1!")
    unknown = _login(client, "enum.unknown@example.test", "WrongPass1!")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json() == unknown.get_json()


def test_a_successful_login_clears_the_failure_count(client, app):
    email = "lockout.reset@example.test"
    _make_user(app, email)
    for _ in range(MAX_FAILED_LOGINS - 1):
        _login(client, email, "WrongPass1!")
    assert _login(client, email).status_code == 200
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        assert user.failed_login_count == 0
        assert user.locked_until is None
        assert user.last_login_at is not None


def test_password_reset_answers_the_same_for_unknown_addresses(client, app):
    _make_user(app, "reset.known@example.test")
    known = client.post("/api/auth/password-reset", json={"email": "reset.known@example.test"})
    unknown = client.post("/api/auth/password-reset", json={"email": "reset.nobody@example.test"})
    assert known.status_code == unknown.status_code == 200
    assert known.get_json() == unknown.get_json()


# ── Reset tokens at rest ────────────────────────────────────────────────────

def test_reset_token_is_stored_hashed(app):
    """A database reader must not be able to use a reset that is in flight."""
    user_id = _make_user(app, "reset.hash@example.test")
    with app.app_context():
        user = db.session.get(User, user_id)
        token = user.generate_reset_token()
        stored = db.session.get(User, user_id).password_reset_token
        assert stored != token, "the clear token reached the database"
        assert len(stored) == 64, "expected a sha256 digest"
        assert User.by_reset_token(token) is not None
        assert User.by_reset_token("not-the-token") is None


def test_expired_reset_token_is_refused(app):
    from datetime import datetime, timedelta
    user_id = _make_user(app, "reset.expired@example.test")
    with app.app_context():
        user = db.session.get(User, user_id)
        token = user.generate_reset_token()
        user.password_reset_expires = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        assert User.by_reset_token(token) is None


def test_reset_revokes_every_session(client, app):
    email = "reset.sessions@example.test"
    _make_user(app, email)
    assert _login(client, email).status_code == 200
    assert _login(client, email).status_code == 200

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        assert UserSession.query.filter_by(user_id=user.id, is_revoked=False).count() >= 2
        token = user.generate_reset_token()

    response = client.post("/api/auth/password-reset/confirm",
                           json={"token": token, "password": "BrandNew123!"})
    assert response.status_code == 200, response.get_json()

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        assert UserSession.query.filter_by(user_id=user.id, is_revoked=False).count() == 0
        assert user.password_reset_token is None


# ── Refresh rotation and reuse detection ────────────────────────────────────

def _tokens(client, email):
    body = _login(client, email).get_json()
    return body["access_token"], body["refresh_token"]


def test_refresh_rotates_the_refresh_token(client, app):
    email = "rotate.basic@example.test"
    _make_user(app, email)
    _access, refresh = _tokens(client, email)

    first = client.post("/api/auth/refresh", headers=auth_header(refresh))
    assert first.status_code == 200, first.get_json()
    rotated = first.get_json()["refresh_token"]
    assert rotated != refresh, "the refresh token was not rotated"

    # The new one works.
    assert client.post("/api/auth/refresh", headers=auth_header(rotated)).status_code == 200


def test_replaying_an_old_refresh_token_kills_the_session(client, app):
    """Two parties holding one token means it was stolen; end the session."""
    email = "rotate.reuse@example.test"
    _make_user(app, email)
    access, refresh = _tokens(client, email)

    rotated = client.post("/api/auth/refresh", headers=auth_header(refresh)).get_json()["refresh_token"]

    replay = client.post("/api/auth/refresh", headers=auth_header(refresh))
    assert replay.status_code == 401, "a superseded refresh token was accepted"

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        session = (UserSession.query.filter_by(user_id=user.id)
                   .order_by(UserSession.id.desc()).first())
        assert session.is_revoked
        assert session.revoked_reason == "refresh_reuse"

    # The session is gone for the legitimate holder too, which is the point:
    # they are signed out visibly rather than sharing the account.
    assert client.post("/api/auth/refresh", headers=auth_header(rotated)).status_code == 401
    assert client.get("/api/auth/profile", headers=auth_header(access)).status_code == 401


def test_refresh_updates_last_active(client, app):
    email = "rotate.active@example.test"
    _make_user(app, email)
    _access, refresh = _tokens(client, email)
    client.post("/api/auth/refresh", headers=auth_header(refresh))
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        session = (UserSession.query.filter_by(user_id=user.id)
                   .order_by(UserSession.id.desc()).first())
        assert session.rotated_at is not None


# ── Changing your own password ──────────────────────────────────────────────

def test_change_password_requires_the_current_one(client, app):
    email = "change.wrong@example.test"
    _make_user(app, email)
    token = _login(client, email).get_json()["access_token"]
    response = client.post("/api/auth/password",
                           json={"current_password": "NotIt1234!", "new_password": "Replaced123!"},
                           headers=auth_header(token))
    assert response.status_code == 400


def test_change_password_signs_out_other_sessions_but_not_this_one(client, app):
    email = "change.sessions@example.test"
    _make_user(app, email)
    other = _login(client, email).get_json()["access_token"]
    current = _login(client, email).get_json()["access_token"]

    response = client.post("/api/auth/password",
                           json={"current_password": PASSWORD, "new_password": "Replaced123!"},
                           headers=auth_header(current))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["other_sessions_signed_out"] >= 1

    assert client.get("/api/auth/profile", headers=auth_header(other)).status_code == 401
    assert client.get("/api/auth/profile", headers=auth_header(current)).status_code == 200


def test_change_password_rejects_a_weak_replacement(client, app):
    email = "change.weak@example.test"
    _make_user(app, email)
    token = _login(client, email).get_json()["access_token"]
    response = client.post("/api/auth/password",
                           json={"current_password": PASSWORD, "new_password": "abc"},
                           headers=auth_header(token))
    assert response.status_code == 400


# ── Administrative changes invalidate tokens ────────────────────────────────

def test_disabling_an_account_revokes_its_sessions(client, app, admin_token):
    email = "admin.disable@example.test"
    user_id = _make_user(app, email)
    victim = _login(client, email).get_json()["access_token"]
    assert client.get("/api/auth/profile", headers=auth_header(victim)).status_code == 200

    response = client.put(f"/api/users/{user_id}", json={"status": "suspended"},
                          headers=auth_header(admin_token))
    assert response.status_code == 200, response.get_json()
    assert client.get("/api/auth/profile", headers=auth_header(victim)).status_code == 401


def test_changing_roles_revokes_sessions(client, app, admin_token):
    """An access token carries the old roles until it expires."""
    email = "admin.roles@example.test"
    user_id = _make_user(app, email)
    victim = _login(client, email).get_json()["access_token"]

    response = client.put(f"/api/users/{user_id}", json={"roles": ["agent"]},
                          headers=auth_header(admin_token))
    assert response.status_code == 200, response.get_json()
    assert client.get("/api/auth/profile", headers=auth_header(victim)).status_code == 401


def test_changing_email_clears_a_pending_reset(client, app, admin_token):
    email = "admin.email@example.test"
    user_id = _make_user(app, email)
    with app.app_context():
        db.session.get(User, user_id).generate_reset_token()

    response = client.put(f"/api/users/{user_id}", json={"email": "admin.email2@example.test"},
                          headers=auth_header(admin_token))
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        assert db.session.get(User, user_id).password_reset_token is None


def test_admin_created_account_must_change_its_password(client, admin_token):
    response = client.post("/api/users/", json={
        "email": "invited.user@example.test", "first_name": "Invited", "last_name": "User",
    }, headers=auth_header(admin_token))
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["must_change_password"] is True
    assert body["temporary_password"]


# ── Response headers ────────────────────────────────────────────────────────

@pytest.mark.parametrize("header,expected", [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
])
def test_security_headers_are_present(client, header, expected):
    response = client.get("/api/geo/zones")
    assert response.headers.get(header) == expected


def test_json_responses_carry_a_locked_down_csp(client):
    response = client.get("/api/geo/zones")
    csp = response.headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_api_responses_are_not_cached(client):
    response = client.get("/api/geo/zones")
    assert response.headers.get("Cache-Control") == "no-store"


def test_permissions_policy_disables_hardware(client):
    policy = client.get("/api/geo/zones").headers.get("Permissions-Policy", "")
    for feature in ("camera=()", "microphone=()", "geolocation=()"):
        assert feature in policy


# ── Rate-limit keying ───────────────────────────────────────────────────────

def test_rate_limit_key_prefers_the_account_over_the_address(client, app):
    """Behind the /api proxy every request shares one address."""
    from app.net import rate_limit_key

    email = "ratekey@example.test"
    _make_user(app, email)
    token = _login(client, email).get_json()["access_token"]

    # A fresh application context each time: `g` is bound to the app context,
    # and the suite holds one open for the whole session, so a JWT verified by
    # an earlier test would otherwise still be visible here.
    with app.app_context():
        with app.test_request_context("/api/shipments/my"):
            assert rate_limit_key().startswith("ip:"), "anonymous traffic keys on the address"

    with app.app_context():
        with app.test_request_context("/api/shipments/my",
                                      headers={"Authorization": f"Bearer {token}"}):
            from flask_jwt_extended import verify_jwt_in_request
            verify_jwt_in_request()
            assert rate_limit_key().startswith("user:"), "a signed-in caller keys on the account"


# ── Agent scoping on tracking events ────────────────────────────────────────

def test_an_agent_cannot_add_events_to_an_unassigned_shipment(client, app, admin_token):
    """Role alone used to be enough to mark a stranger's parcel delivered."""
    customer = register_and_login(client, "scoped.customer@example.test")
    created = client.post("/api/shipments", json={
        "origin": "Kathmandu", "destination": "Pokhara",
    }, headers=auth_header(customer))
    assert created.status_code == 201
    tracking_no = created.get_json()["tracking_no"]

    _make_user(app, "scoped.agent@example.test", role="agent")
    agent = _login(client, "scoped.agent@example.test").get_json()["access_token"]

    response = client.post(f"/api/tracking/{tracking_no}/events",
                           json={"status": "delivered", "location": "x", "description": "y"},
                           headers=auth_header(agent))
    assert response.status_code == 403, "an unassigned agent changed a shipment"


def test_tracking_events_reject_an_illegal_transition(client, admin_token):
    customer = register_and_login(client, "scoped.customer2@example.test")
    created = client.post("/api/shipments", json={
        "origin": "Kathmandu", "destination": "Pokhara",
    }, headers=auth_header(customer))
    tracking_no = created.get_json()["tracking_no"]

    deliver = client.post(f"/api/tracking/{tracking_no}/events",
                          json={"status": "delivered", "location": "x", "description": "y"},
                          headers=auth_header(admin_token))
    assert deliver.status_code == 201

    reopen = client.post(f"/api/tracking/{tracking_no}/events",
                         json={"status": "pending", "location": "x", "description": "y"},
                         headers=auth_header(admin_token))
    assert reopen.status_code == 409


# ── Forwarded client address ────────────────────────────────────────────────

def test_client_ip_ignores_the_header_when_no_proxy_secret_is_set(app):
    """With no shared secret there is no proxy to believe, so the peer wins."""
    from app.net import client_ip

    with app.app_context():
        previous = app.config["BACKEND_PROXY_SECRET"]
        app.config["BACKEND_PROXY_SECRET"] = ""
        try:
            with app.test_request_context(
                "/api/health",
                headers={"X-Real-Client-IP": "203.0.113.9"},
                environ_base={"REMOTE_ADDR": "10.0.0.5"},
            ):
                assert client_ip() == "10.0.0.5", "an unauthenticated header was trusted"
        finally:
            app.config["BACKEND_PROXY_SECRET"] = previous


def test_client_ip_uses_the_header_behind_the_authenticated_proxy(app):
    from app.net import client_ip

    with app.app_context():
        previous = app.config["BACKEND_PROXY_SECRET"]
        app.config["BACKEND_PROXY_SECRET"] = "proxy-shared-secret"
        try:
            with app.test_request_context(
                "/api/health",
                headers={"X-Real-Client-IP": "203.0.113.9"},
                environ_base={"REMOTE_ADDR": "10.0.0.5"},
            ):
                assert client_ip() == "203.0.113.9"
        finally:
            app.config["BACKEND_PROXY_SECRET"] = previous


def test_edge_rejected_requests_are_not_audited(app):
    """This hook runs before the limiter, so logging rejects is unbounded."""
    from app.models.audit_log import AuditLog

    client = app.test_client()
    previous = app.config["BACKEND_PROXY_SECRET"]
    app.config["BACKEND_PROXY_SECRET"] = "gate-key"
    try:
        with app.app_context():
            before = AuditLog.query.count()
        for _ in range(5):
            assert client.get("/api/shipments/my").status_code == 403
        with app.app_context():
            assert AuditLog.query.count() == before, "rejected traffic wrote audit rows"
    finally:
        app.config["BACKEND_PROXY_SECRET"] = previous
