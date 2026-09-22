"""
Shared pytest fixtures for the test suite.
Using session scope so the Flask-RestX global Api object is only
initialized once — creating multiple apps in the same process causes
'add_url_rule called after first request' errors.
"""
import pytest
from app import create_app
from app.extensions import db as _db


@pytest.fixture(scope="session")
def app():
    a = create_app("testing")
    with a.app_context():
        _db.create_all()
        yield a
        _db.drop_all()


@pytest.fixture(scope="session")
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def admin_token(app, client):
    """
    Returns an access token for a guaranteed super_admin user.
    Creates the user directly via SQLAlchemy so it bypasses the
    'first registered user gets super_admin' heuristic — which is
    unreliable in a shared session-scoped DB where other test modules
    register users first.
    """
    email = "fixture_admin@test.com"
    password = "Admin1234!"
    with app.app_context():
        from app.models.user import User, UserStatus
        from app.models.role import Role
        # Idempotent — only create if the user doesn't exist yet
        if not User.query.filter_by(email=email).first():
            base = email.split("@")[0]
            uname = base
            i = 1
            while User.query.filter_by(username=uname).first():
                uname = f"{base}{i}"; i += 1
            u = User(
                username=uname,
                email=email,
                first_name="Fixture",
                last_name="Admin",
                status=UserStatus.ACTIVE,
            )
            u.set_password(password)
            role = Role.query.filter_by(name="super_admin").first()
            if role:
                u.roles.append(role)
            _db.session.add(u)
            _db.session.commit()
    # Login via the API to get a real JWT
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    j = r.get_json()
    assert r.status_code == 200, f"Admin fixture login failed: {j}"
    return j["access_token"]


# ── helpers used across multiple test files ──────────────────────────────────

def _register(client, email, password="Admin1234!"):
    return client.post("/api/auth/register", json={
        "email": email, "password": password,
        "first_name": "Test", "last_name": "User",
    })


def _login(client, email, password="Admin1234!"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    j = r.get_json()
    assert r.status_code == 200, f"Login failed for {email}: {j}"
    return j["access_token"]


def register_and_login(client, email, password="Admin1234!"):
    _register(client, email, password)
    return _login(client, email, password)


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}
