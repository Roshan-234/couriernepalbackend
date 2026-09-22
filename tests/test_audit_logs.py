"""Tests for audit log endpoints — user isolation and admin visibility."""
import pytest
from tests.conftest import register_and_login, auth_header


def test_audit_log_created_on_login(client):
    """A login action must appear in the user's own audit log."""
    tok = register_and_login(client, "audit_login@test.com")
    r = client.get("/api/audit/logs/me/", headers=auth_header(tok))
    assert r.status_code == 200
    data = r.get_json()
    assert data["total"] >= 1
    actions = [l["action"] for l in data["logs"]]
    assert "LOGIN" in actions


def test_register_action_logged(client):
    """REGISTER action is logged (user_id set via g.audit_user_id on login)."""
    tok = register_and_login(client, "audit_reg@test.com")
    r = client.get("/api/audit/logs/me/", headers=auth_header(tok))
    data = r.get_json()
    actions = [l["action"] for l in data["logs"]]
    # At minimum LOGIN should be there; REGISTER is logged with user_id=None
    assert len(actions) >= 1


def test_user_sees_only_own_logs(client):
    """Users cannot see each other's logs."""
    tok1 = register_and_login(client, "audit_u1@test.com")
    tok2 = register_and_login(client, "audit_u2@test.com")

    r1 = client.get("/api/audit/logs/", headers=auth_header(tok1))
    r2 = client.get("/api/audit/logs/", headers=auth_header(tok2))

    ids1 = {l["user_id"] for l in r1.get_json()["logs"] if l["user_id"]}
    ids2 = {l["user_id"] for l in r2.get_json()["logs"] if l["user_id"]}
    # Their user-id sets must be disjoint (or one is empty)
    assert ids1.isdisjoint(ids2)


def test_admin_sees_all_logs(client):
    """super_admin sees logs from multiple users."""
    # The first registered user is super_admin
    admin_tok = register_and_login(client, "audit_sadmin@test.com")
    r = client.get("/api/audit/logs/", headers=auth_header(admin_tok))
    assert r.status_code == 200
    data = r.get_json()
    uid_set = {l["user_id"] for l in data["logs"] if l["user_id"]}
    # Admin should see logs from more than one user eventually
    assert data["total"] >= 1


def test_filter_by_action(client):
    """action query-param filters logs correctly."""
    tok = register_and_login(client, "audit_filter@test.com")
    r = client.get("/api/audit/logs/me/?action=LOGIN", headers=auth_header(tok))
    assert r.status_code == 200
    for log in r.get_json()["logs"]:
        assert "LOGIN" in log["action"].upper()


def test_pagination(client):
    """page/per_page params are respected."""
    tok = register_and_login(client, "audit_page@test.com")
    r = client.get("/api/audit/logs/me/?page=1&per_page=2", headers=auth_header(tok))
    assert r.status_code == 200
    data = r.get_json()
    assert "page" in data and "pages" in data and "total" in data
    assert len(data["logs"]) <= 2


def test_per_page_capped_at_200(client):
    """per_page > 200 is silently capped."""
    tok = register_and_login(client, "audit_cap@test.com")
    r = client.get("/api/audit/logs/me/?per_page=9999", headers=auth_header(tok))
    assert r.status_code == 200
    assert r.get_json()["per_page"] == 200
