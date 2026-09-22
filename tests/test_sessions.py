"""Tests for session management — creation, listing, revocation, blocklisting."""
import pytest
from tests.conftest import register_and_login, auth_header


def test_login_creates_session(client):
    """Logging in should result in at least one active session."""
    tok = register_and_login(client, "sess_create@test.com")
    r = client.get("/api/sessions/", headers=auth_header(tok))
    assert r.status_code == 200
    sessions = r.get_json()
    assert isinstance(sessions, list)
    assert len(sessions) >= 1


def test_session_fields_correct(client):
    """Session objects contain expected fields."""
    tok = register_and_login(client, "sess_fields@test.com")
    r = client.get("/api/sessions/", headers=auth_header(tok))
    sessions = r.get_json()
    assert len(sessions) >= 1
    s = sessions[0]
    assert "id" in s
    assert "jti" in s
    assert "created_at" in s
    assert s["is_revoked"] is False


def test_logout_revokes_token(client):
    """After logout, the same token must be rejected (401)."""
    tok = register_and_login(client, "sess_logout@test.com")
    # Confirm authenticated before logout
    assert client.get("/api/sessions/", headers=auth_header(tok)).status_code == 200
    # Logout
    r = client.post("/api/auth/logout", headers=auth_header(tok))
    assert r.status_code == 200
    # Token is now revoked — any protected endpoint must return 401
    r2 = client.get("/api/sessions/", headers=auth_header(tok))
    assert r2.status_code == 401


def test_revoked_token_blocked_on_audit_logs(client):
    """Revoked token is blocked on audit log endpoint too."""
    tok = register_and_login(client, "sess_revoke2@test.com")
    client.post("/api/auth/logout", headers=auth_header(tok))
    r = client.get("/api/audit/logs/me/", headers=auth_header(tok))
    assert r.status_code == 401


def test_revoke_specific_session(client):
    """DELETE /sessions/<id> revokes that session."""
    tok = register_and_login(client, "sess_specific@test.com")
    sessions = client.get("/api/sessions/", headers=auth_header(tok)).get_json()
    assert len(sessions) >= 1
    sid = sessions[0]["id"]
    r = client.delete(f"/api/sessions/{sid}", headers=auth_header(tok))
    assert r.status_code == 200
    # Token (which was this session) should now be invalid
    r2 = client.get("/api/sessions/", headers=auth_header(tok))
    assert r2.status_code == 401


def test_revoke_all_sessions(client):
    """DELETE /sessions/all revokes all of user's sessions."""
    tok = register_and_login(client, "sess_all@test.com")
    r = client.delete("/api/sessions/all", headers=auth_header(tok))
    assert r.status_code == 200
    # Token now invalid
    r2 = client.get("/api/sessions/", headers=auth_header(tok))
    assert r2.status_code == 401
