"""
Tests for cookie-based auth + CSRF + session tracking added for production.

The backend accepts JWTs from both the Authorization header (API clients,
tests, server-to-server) and from HttpOnly cookies (the browser). These tests
exercise the cookie path end-to-end using isolated test clients (each gets its
own cookie jar) so login/refresh/logout cookie flows don't bleed across tests.
"""
import uuid


def _set_cookie_headers(resp):
    return resp.headers.getlist("Set-Cookie")


def _find_cookie(set_cookie_headers, name):
    """Return the raw Set-Cookie string for a named cookie, or None."""
    for h in set_cookie_headers:
        if h.startswith(f"{name}="):
            return h
    return None


def _cookie_value(set_cookie_headers, name):
    raw = _find_cookie(set_cookie_headers, name)
    if not raw:
        return None
    return raw.split("=", 1)[1].split(";", 1)[0]


def _register_and_get_creds(app):
    email = f"cookie_{uuid.uuid4().hex[:10]}@test.com"
    password = "Admin1234!"
    c = app.test_client()
    r = c.post("/api/auth/register", json={
        "email": email, "password": password,
        "first_name": "Cookie", "last_name": "Test",
    })
    assert r.status_code == 201, r.get_json()
    return email, password


def test_login_sets_httponly_access_and_readable_csrf_cookies(app):
    email, password = _register_and_get_creds(app)
    c = app.test_client()
    resp = c.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200

    cookies = _set_cookie_headers(resp)
    access = _find_cookie(cookies, "access_token")
    csrf = _find_cookie(cookies, "csrf_access_token")
    refresh = _find_cookie(cookies, "refresh_token_cookie")

    assert access is not None, "access_token cookie must be set"
    assert "HttpOnly" in access, "access token cookie must be HttpOnly"
    assert csrf is not None, "csrf_access_token cookie must be set"
    assert "HttpOnly" not in csrf, "CSRF cookie must be readable by JS"
    assert refresh is not None, "refresh cookie must be set"
    assert "HttpOnly" in refresh, "refresh cookie must be HttpOnly"


def test_profile_works_with_cookie_only(app):
    email, password = _register_and_get_creds(app)
    c = app.test_client()
    c.post("/api/auth/login", json={"email": email, "password": password})
    # No Authorization header — cookie jar carries the access_token cookie.
    resp = c.get("/api/auth/profile")
    assert resp.status_code == 200
    assert resp.get_json()["email"] == email


def test_cookie_post_requires_csrf_header(app):
    email, password = _register_and_get_creds(app)
    c = app.test_client()
    login = c.post("/api/auth/login", json={"email": email, "password": password})
    csrf = _cookie_value(_set_cookie_headers(login), "csrf_access_token")

    # Cookie present but no X-CSRF-TOKEN header -> rejected.
    missing = c.post("/api/auth/logout")
    assert missing.status_code == 401

    # With the CSRF header it succeeds.
    ok = c.post("/api/auth/logout", headers={"X-CSRF-TOKEN": csrf})
    assert ok.status_code == 200


def test_logout_clears_cookies(app):
    email, password = _register_and_get_creds(app)
    c = app.test_client()
    login = c.post("/api/auth/login", json={"email": email, "password": password})
    csrf = _cookie_value(_set_cookie_headers(login), "csrf_access_token")

    resp = c.post("/api/auth/logout", headers={"X-CSRF-TOKEN": csrf})
    assert resp.status_code == 200
    cookies = _set_cookie_headers(resp)
    access = _find_cookie(cookies, "access_token")
    # Unset cookies are sent back with an empty value / expiry in the past.
    assert access is not None
    assert access.startswith("access_token=;") or "Expires=Thu, 01 Jan 1970" in access


def test_refresh_reattaches_roles_and_sets_cookie(app):
    import jwt as pyjwt
    email, password = _register_and_get_creds(app)
    c = app.test_client()
    login = c.post("/api/auth/login", json={"email": email, "password": password})
    refresh_csrf = _cookie_value(_set_cookie_headers(login), "csrf_refresh_token")

    resp = c.post("/api/auth/refresh", headers={"X-CSRF-TOKEN": refresh_csrf})
    assert resp.status_code == 200
    body = resp.get_json()
    assert "access_token" in body

    # New access token must carry the roles claim (regression: refresh used to drop it).
    decoded = pyjwt.decode(body["access_token"], options={"verify_signature": False})
    assert "roles" in decoded
    assert decoded["roles"] == ["customer"]

    # And it must set a fresh access_token cookie.
    assert _find_cookie(_set_cookie_headers(resp), "access_token") is not None


def test_header_auth_still_works_without_csrf(app, client):
    # Bearer-header requests bypass CSRF (browsers can't forge them).
    from tests.conftest import register_and_login, auth_header
    token = register_and_login(client, f"hdr_{uuid.uuid4().hex[:8]}@test.com")
    resp = client.get("/api/auth/profile", headers=auth_header(token))
    assert resp.status_code == 200
