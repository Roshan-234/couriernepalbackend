"""Public endpoints must not hand out staff contact details or secrets."""
from tests.conftest import auth_header


def _category_id(client, admin_token):
    """Posts require a category, so make sure one exists."""
    existing = client.get("/api/blog/categories").get_json()
    rows = existing if isinstance(existing, list) else existing.get("data", [])
    if rows:
        return rows[0]["id"]
    created = client.post("/api/blog/categories", json={
        "name": "Shipping guides", "slug": "shipping-guides",
    }, headers=auth_header(admin_token))
    assert created.status_code in (200, 201), created.get_json()
    return created.get_json()["id"]


def _create_post(client, admin_token, slug):
    response = client.post("/api/blog/posts", json={
        "title": f"Shipping guide {slug}",
        "slug": slug,
        "content": "How to pack a parcel for Nepal.",
        "excerpt": "Packing guidance.",
        "status": "published",
        "category_id": _category_id(client, admin_token),
    }, headers=auth_header(admin_token))
    assert response.status_code in (200, 201), response.get_json()
    # The API slugifies the title itself, so trust what it returns.
    return response.get_json()["slug"]


def test_published_post_does_not_leak_author_email(client, admin_token, app):
    """An anonymous reader gets a display name, never the editor's address."""
    slug = _create_post(client, admin_token, "leak-check-detail")

    with app.app_context():
        from app.models.user import User
        admin = User.query.filter_by(email="fixture_admin@test.com").first()
        assert admin is not None
        email = admin.email

    detail = client.get(f"/api/blog/posts/{slug}")
    assert detail.status_code == 200
    body = detail.get_data(as_text=True)
    assert email not in body, "author email reached an anonymous reader"

    author = detail.get_json().get("author")
    assert author is not None
    assert "email" not in author
    assert author.get("name")


def test_published_list_does_not_leak_author_email(client, admin_token, app):
    _create_post(client, admin_token, "leak-check-list")

    with app.app_context():
        from app.models.user import User
        email = User.query.filter_by(email="fixture_admin@test.com").first().email

    listing = client.get("/api/blog/posts?status=published")
    assert listing.status_code == 200
    payload = listing.get_json()
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    assert rows, "no published posts returned, so this test proved nothing"
    assert email not in listing.get_data(as_text=True)


def test_public_carrier_list_has_no_credentials(client):
    """Carrier connectors are public for the tracking page; secrets are not."""
    response = client.get("/api/carriers")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for forbidden in ("api_key_encrypted", "api_secret_encrypted",
                      "password_encrypted", "webhook_secret_encrypted",
                      "client_secret_encrypted"):
        assert forbidden not in body, f"{forbidden} exposed on the public carrier list"


def test_public_gateway_list_has_no_credentials(client):
    response = client.get("/api/payments/gateways")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for forbidden in ("secret_key_encrypted", "client_secret_encrypted",
                      "public_key_encrypted"):
        assert forbidden not in body, f"{forbidden} exposed on the public gateway list"
