"""Tests for tracking provider CRUD (admin-gated)."""
import pytest
from tests.conftest import register_and_login, auth_header

# admin_token is provided by the session-scoped fixture in conftest.py

# ── tests ────────────────────────────────────────────────────────────────────

def test_list_providers_public(client):
    """GET /tracking-providers/ is public and never exposes api_key."""
    r = client.get("/api/tracking-providers/")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, list)
    for p in data:
        assert "api_key" not in p


def test_create_provider_requires_auth(client):
    """Creating a provider without a token is rejected."""
    r = client.post("/api/tracking-providers/", json={
        "name": "NoAuth", "code": "noauth",
    })
    assert r.status_code in (401, 403)


def test_create_provider_as_admin(client, admin_token):
    """Admin can create a new provider."""
    r = client.post("/api/tracking-providers/", json={
        "name": "TestCourier", "code": "testcourier",
        "api_url_template": "https://example.com/track/{tracking_no}",
        "is_active": True,
        "supported_countries": "all",
    }, headers=auth_header(admin_token))
    assert r.status_code == 201, r.get_json()
    data = r.get_json()
    assert data["code"] == "testcourier"


def test_create_duplicate_code_rejected(client, admin_token):
    """Duplicate code returns 409."""
    r = client.post("/api/tracking-providers/", json={
        "name": "TestCourier Dup", "code": "testcourier",
    }, headers=auth_header(admin_token))
    assert r.status_code == 409


def test_update_provider(client, admin_token):
    """Admin can update provider details."""
    r = client.put("/api/tracking-providers/testcourier", json={
        "name": "TestCourier Updated", "is_active": False,
    }, headers=auth_header(admin_token))
    assert r.status_code == 200
    assert r.get_json()["name"] == "TestCourier Updated"


def test_deactivate_provider(client, admin_token):
    """DELETE deactivates provider; it no longer appears in public list."""
    client.put("/api/tracking-providers/testcourier", json={"is_active": True},
               headers=auth_header(admin_token))
    r = client.delete("/api/tracking-providers/testcourier",
                      headers=auth_header(admin_token))
    assert r.status_code == 200
    codes = [p["code"] for p in client.get("/api/tracking-providers/").get_json()]
    assert "testcourier" not in codes


def test_admin_list_all_includes_inactive(client, admin_token):
    """GET /tracking-providers/all returns all providers including inactive."""
    r = client.get("/api/tracking-providers/all", headers=auth_header(admin_token))
    assert r.status_code == 200
    codes = [p["code"] for p in r.get_json()]
    assert "testcourier" in codes


def test_admin_list_exposes_api_url_template(client, admin_token):
    """Admin list includes api_url_template (secrets)."""
    r = client.get("/api/tracking-providers/all", headers=auth_header(admin_token))
    data = r.get_json()
    assert any(p.get("api_url_template") for p in data)
