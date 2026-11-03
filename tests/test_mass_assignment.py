"""Write-boundary tests for the admin CRUD modules.

Each handler covered here used to hand raw client JSON to a model constructor
or a blind setattr loop, so a caller could name any column — 'id' included —
and an unexpected key raised a TypeError and returned 500. These tests assert
the behaviour, not the shape of the whitelist: the row's id does not move, an
unknown key comes back as a 400 that names it, and a deactivated row is
invisible to anyone but an administrator.
"""
from uuid import uuid4

import pytest

from tests.conftest import auth_header, register_and_login

PASSWORD = "Admin1234!"


@pytest.fixture
def admin(admin_token):
    return auth_header(admin_token)


@pytest.fixture
def anon(app):
    """A client with no cookies, so 'anonymous' really is anonymous."""
    return app.test_client()


def _tag():
    """The test database is session-scoped and shared, so keys must be unique."""
    return uuid4().hex[:8]


def _message(response):
    body = response.get_json() or {}
    return str(body.get("message", body))


def _create_service(client, admin, **overrides):
    payload = {"name": "Air freight", "code": f"AIR-{_tag()}", "description": "Fast"}
    payload.update(overrides)
    return client.post("/api/services", json=payload, headers=admin)


def _create_faq(client, admin, **overrides):
    payload = {"question": f"How long does {_tag()} take?", "answer": "Three days."}
    payload.update(overrides)
    return client.post("/api/faq", json=payload, headers=admin)


def _category_id(client, admin):
    existing = client.get("/api/blog/categories").get_json()
    rows = existing if isinstance(existing, list) else existing.get("data", [])
    if rows:
        return rows[0]["id"]
    created = client.post("/api/blog/categories", json={"name": f"Guides {_tag()}"}, headers=admin)
    assert created.status_code in (200, 201), created.get_json()
    return created.get_json()["id"]


def _user_id(app, email):
    from app.models.user import User

    with app.app_context():
        return User.query.filter_by(email=email).one().id


# ── Services ─────────────────────────────────────────────────────────────────

def test_service_create_cannot_choose_its_own_id(client, admin):
    created = _create_service(client, admin, id=9999)
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["id"] != 9999
    assert client.get("/api/services/9999").status_code == 404


def test_service_update_cannot_rewrite_id(client, admin):
    service = _create_service(client, admin).get_json()
    updated = client.put(f"/api/services/{service['id']}",
                         json={"id": 9999, "name": "Renamed"}, headers=admin)
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["id"] == service["id"]
    assert updated.get_json()["name"] == "Renamed"
    assert client.get("/api/services/9999").status_code == 404


def test_service_rejects_unknown_field_by_name(client, admin):
    created = _create_service(client, admin, prize_range="cheap")
    assert created.status_code == 400
    assert "prize_range" in _message(created)


def test_service_accepts_the_icon_the_swagger_model_advertises(client, admin):
    """'icon' is documented but is not a column; it must not raise a 500."""
    created = _create_service(client, admin, icon="plane")
    assert created.status_code == 201, created.get_json()


def test_service_rejects_overlong_name(client, admin):
    created = _create_service(client, admin, name="x" * 300)
    assert created.status_code == 400


def test_service_rejects_non_boolean_is_active(client, admin):
    created = _create_service(client, admin, is_active="yes")
    assert created.status_code == 400


def test_inactive_service_is_invisible_to_anonymous_callers(client, admin, anon):
    service = _create_service(client, admin).get_json()
    assert client.put(f"/api/services/{service['id']}",
                      json={"is_active": False}, headers=admin).status_code == 200

    assert anon.get(f"/api/services/{service['id']}").status_code == 404
    assert client.get(f"/api/services/{service['id']}", headers=admin).status_code == 200


# ── FAQ ──────────────────────────────────────────────────────────────────────

def test_faq_create_cannot_choose_its_own_id(client, admin):
    created = _create_faq(client, admin, id=9999)
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["id"] != 9999
    assert client.get("/api/faq/9999").status_code == 404


def test_faq_rejects_unknown_field_by_name(client, admin):
    created = _create_faq(client, admin, anwser="typo")
    assert created.status_code == 400
    assert "anwser" in _message(created)


def test_faq_order_must_be_an_integer(client, admin):
    assert _create_faq(client, admin, order="first").status_code == 400
    assert _create_faq(client, admin, order=3).status_code == 201


def test_inactive_faq_is_invisible_to_anonymous_callers(client, admin, anon):
    faq = _create_faq(client, admin).get_json()
    assert client.put(f"/api/faq/{faq['id']}",
                      json={"is_active": False}, headers=admin).status_code == 200

    assert anon.get(f"/api/faq/{faq['id']}").status_code == 404
    assert client.get(f"/api/faq/{faq['id']}", headers=admin).status_code == 200


# ── Warehouses ───────────────────────────────────────────────────────────────

def test_warehouse_rejects_unknown_field_by_name(client, admin):
    created = client.post("/api/warehouses", json={
        "name": "Hub", "code": f"WH-{_tag()}", "manager_id": 3,
    }, headers=admin)
    assert created.status_code == 400
    assert "manager_id" in _message(created)


def test_warehouse_bounds_string_lengths(client, admin):
    created = client.post("/api/warehouses", json={
        "name": "Hub", "code": f"WH-{_tag()}", "city": "x" * 500,
    }, headers=admin)
    assert created.status_code == 400


def test_warehouse_create_cannot_choose_its_own_id(client, admin):
    created = client.post("/api/warehouses", json={
        "name": "Hub", "code": f"WH-{_tag()}", "id": 9999,
    }, headers=admin)
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["id"] != 9999


# ── Agents ───────────────────────────────────────────────────────────────────

def test_agent_create_with_the_old_swagger_fields_is_a_400_not_a_500(client, admin, app):
    """agent_code/state/country/postal_code/status were never columns."""
    email = f"agent_legacy_{_tag()}@test.com"
    register_and_login(client, email)
    created = client.post("/api/agents", json={
        "user_id": _user_id(app, email),
        "company_name": "Himalaya Logistics",
        "agent_code": "AG-1",
        "state": "Bagmati",
        "country": "Nepal",
        "postal_code": "44600",
        "status": "active",
    }, headers=admin)
    assert created.status_code == 400, created.get_json()
    message = _message(created)
    for field in ("agent_code", "country", "postal_code", "state", "status"):
        assert field in message


def test_agent_profile_user_id_is_frozen_after_creation(client, admin, app):
    owner = f"agent_owner_{_tag()}@test.com"
    other = f"agent_other_{_tag()}@test.com"
    register_and_login(client, owner)
    register_and_login(client, other)
    owner_id, other_id = _user_id(app, owner), _user_id(app, other)

    created = client.post("/api/agents", json={
        "user_id": owner_id, "company_name": "Himalaya Logistics", "phone": "+9779800000000",
    }, headers=admin)
    assert created.status_code == 201, created.get_json()
    agent_id = created.get_json()["id"]

    hijack = client.put(f"/api/agents/{agent_id}", json={"user_id": other_id}, headers=admin)
    assert hijack.status_code == 400
    assert client.get(f"/api/agents/{agent_id}", headers=admin).get_json()["user_id"] == owner_id

    # Echoing the current owner back is fine; it changes nothing.
    renamed = client.put(f"/api/agents/{agent_id}",
                         json={"user_id": owner_id, "company_name": "Annapurna Cargo"}, headers=admin)
    assert renamed.status_code == 200, renamed.get_json()
    assert renamed.get_json()["company_name"] == "Annapurna Cargo"


def test_agent_profile_requires_a_real_unclaimed_user(client, admin, app):
    email = f"agent_dup_{_tag()}@test.com"
    register_and_login(client, email)
    user_id = _user_id(app, email)

    assert client.post("/api/agents", json={"user_id": 10**9}, headers=admin).status_code == 400
    assert client.post("/api/agents", json={"user_id": "seven"}, headers=admin).status_code == 400
    assert client.post("/api/agents", json={"user_id": user_id}, headers=admin).status_code == 201
    assert client.post("/api/agents", json={"user_id": user_id}, headers=admin).status_code == 409


# ── Blog ─────────────────────────────────────────────────────────────────────

def test_blog_post_rejects_absurdly_long_content(client, admin):
    created = client.post("/api/blog/posts", json={
        "title": f"Packing guide {_tag()}",
        "content": "x" * 200_000,
        "category_id": _category_id(client, admin),
    }, headers=admin)
    assert created.status_code == 400


def test_blog_post_rejects_unknown_field_by_name(client, admin):
    created = client.post("/api/blog/posts", json={
        "title": f"Packing guide {_tag()}",
        "content": "How to pack a parcel.",
        "category_id": _category_id(client, admin),
        "is_featured": True,
    }, headers=admin)
    assert created.status_code == 400
    assert "is_featured" in _message(created)


def test_blog_post_rejects_a_javascript_featured_image(client, admin):
    payload = {
        "title": f"Packing guide {_tag()}",
        "content": "How to pack a parcel.",
        "category_id": _category_id(client, admin),
        "featured_image": "javascript:alert(1)",
    }
    assert client.post("/api/blog/posts", json=payload, headers=admin).status_code == 400

    payload["featured_image"] = "https://cdn.example.test/parcel.png"
    assert client.post("/api/blog/posts", json=payload, headers=admin).status_code == 201


def test_blog_post_cannot_set_its_own_id_or_view_count(client, admin):
    created = client.post("/api/blog/posts", json={
        "title": f"Packing guide {_tag()}",
        "content": "How to pack a parcel.",
        "category_id": _category_id(client, admin),
        "id": 9999,
        "views": 50_000,
    }, headers=admin)
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body["id"] != 9999
    assert body["views"] in (0, None)


def test_blog_post_with_an_unsluggable_title_still_saves(client, admin):
    """A title of pure punctuation slugifies to '', which must not collide."""
    category_id = _category_id(client, admin)
    slugs = set()
    for _ in range(2):
        created = client.post("/api/blog/posts", json={
            "title": "!!! ???", "content": "Punctuation only.", "category_id": category_id,
        }, headers=admin)
        assert created.status_code == 201, created.get_json()
        slugs.add(created.get_json()["slug"])
    assert len(slugs) == 2


# ── Audit log query parameters ───────────────────────────────────────────────

def test_audit_rejects_junk_query_parameters(client, admin):
    for query in ("page=abc", "per_page=lots", "page=0", "date_from=yesterday",
                  "date_to=soon", "user_id=me"):
        response = client.get(f"/api/audit/logs/?{query}", headers=admin)
        assert response.status_code == 400, f"{query} returned {response.status_code}"


def test_audit_accepts_valid_query_parameters(client, admin):
    response = client.get(
        "/api/audit/logs/?page=1&per_page=5&date_from=2020-01-01&date_to=2999-01-01",
        headers=admin,
    )
    assert response.status_code == 200, response.get_json()


def test_audit_me_rejects_junk_pagination(client):
    token = register_and_login(client, f"audit_junk_{_tag()}@test.com")
    assert client.get("/api/audit/logs/me/?page=abc", headers=auth_header(token)).status_code == 400


def test_plain_admin_sees_every_users_logs(client, app):
    """Defect 4: 'admin' and 'super_admin' both see all logs, as the docstring says."""
    from app.extensions import db
    from app.models.role import Role
    from app.models.user import User, UserStatus

    email = f"ops_admin_{_tag()}@test.com"
    with app.app_context():
        user = User(username=email.split("@")[0], email=email, first_name="Ops",
                    last_name="Admin", status=UserStatus.ACTIVE)
        user.set_password(PASSWORD)
        user.roles.append(Role.query.filter_by(name="admin").one())
        db.session.add(user)
        db.session.commit()
        admin_id = user.id

    # Another account's login gives the admin something to see.
    other_email = f"ops_customer_{_tag()}@test.com"
    register_and_login(client, other_email)

    login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.get_json()
    logs = client.get("/api/audit/logs/?per_page=200", headers=auth_header(login.get_json()["access_token"]))
    assert logs.status_code == 200, logs.get_json()
    owners = {row["user_id"] for row in logs.get_json()["logs"] if row["user_id"]}
    assert owners - {admin_id}, "a plain admin saw only their own logs"


# ── Contact ──────────────────────────────────────────────────────────────────

def test_contact_rejects_a_payload_that_is_not_an_object(client):
    assert client.post("/api/contact", json=["name", "email"]).status_code == 400
    assert client.post("/api/contact", json="hello").status_code == 400


def test_contact_rejects_non_string_fields(client):
    response = client.post("/api/contact", json={
        "name": 5, "email": "someone@example.test", "message": "Hello",
    })
    assert response.status_code == 400
