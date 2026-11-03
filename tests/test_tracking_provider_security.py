"""Security regressions for the legacy tracking-provider module.

Three defects are pinned here behaviourally rather than by inspection:
the API key must never leave the process in clear, an admin must not be
able to store a URL aimed at our own network, and the request body must
not be able to reach a column that is not on the writable list.
"""
from tests.conftest import auth_header

CODE = "sectest"
SECRET = "SUPER-SECRET-KEY-abc12345"
ROTATED = "ROTATED-KEY-xyz98765"


def _create(client, admin_token, **overrides):
    payload = {
        "name": "SecTest Courier",
        "code": CODE,
        "api_url_template": "https://example.com/track/{tracking_no}",
        "api_key": SECRET,
        "is_active": True,
    }
    payload.update(overrides)
    return client.post("/api/tracking-providers/", json=payload,
                       headers=auth_header(admin_token))


def _put(client, admin_token, payload):
    return client.put(f"/api/tracking-providers/{CODE}", json=payload,
                      headers=auth_header(admin_token))


# ── Defect 1: the key must never be serialized in clear ──────────────────────

def test_create_response_never_contains_the_plaintext_key(client, admin_token):
    r = _create(client, admin_token)
    assert r.status_code == 201, r.get_json()
    assert SECRET not in r.data.decode()
    body = r.get_json()
    assert "api_key" not in body
    assert body["api_key_masked"].endswith(SECRET[-4:])
    assert SECRET[:-4] not in body["api_key_masked"]


def test_no_endpoint_returning_a_provider_leaks_the_plaintext_key(client, admin_token):
    """Every read path, public and admin, is checked against the raw body."""
    responses = {
        "public list": client.get("/api/tracking-providers/"),
        "admin list": client.get("/api/tracking-providers/all",
                                 headers=auth_header(admin_token)),
        "detail": client.get(f"/api/tracking-providers/{CODE}"),
        "update": _put(client, admin_token, {"name": "SecTest Courier"}),
    }
    for label, r in responses.items():
        assert r.status_code == 200, f"{label}: {r.status_code}"
        assert SECRET not in r.data.decode(), f"{label} leaked the api_key"


def test_column_holds_ciphertext_not_the_key(app, admin_token):
    """Read the row directly: a database dump must not hand over the key."""
    with app.app_context():
        from app.models.tracking_provider import TrackingProvider
        p = TrackingProvider.query.filter_by(code=CODE).first()
        assert p is not None
        assert p.api_key_encrypted != SECRET
        assert SECRET not in p.api_key_encrypted
        assert p.api_key_encrypted.startswith("enc:v1:")
        # ...and the descriptor still round-trips it for application code.
        assert p.api_key == SECRET


# ── Defect 2: stored SSRF ────────────────────────────────────────────────────

def test_loopback_url_is_rejected(client, admin_token):
    r = _put(client, admin_token,
             {"api_url_template": "http://127.0.0.1:6379/track/{tracking_no}"})
    assert r.status_code == 400
    assert "api_url_template" in r.data.decode()


def test_private_range_url_is_rejected(client, admin_token):
    assert _put(client, admin_token,
                {"webhook_url": "http://10.1.2.3/hook"}).status_code == 400
    assert _put(client, admin_token,
                {"logo_url": "http://169.254.169.254/latest/meta-data/"}).status_code == 400


def test_non_http_scheme_is_rejected(client, admin_token):
    assert _put(client, admin_token,
                {"logo_url": "file:///etc/passwd"}).status_code == 400


def test_rejected_url_is_not_stored(client, admin_token):
    """A refused write leaves the previous value in place."""
    r = client.get("/api/tracking-providers/all", headers=auth_header(admin_token))
    before = next(p for p in r.get_json() if p["code"] == CODE)["api_url_template"]
    _put(client, admin_token, {"api_url_template": "http://localhost/track/{tracking_no}"})
    r = client.get("/api/tracking-providers/all", headers=auth_header(admin_token))
    after = next(p for p in r.get_json() if p["code"] == CODE)["api_url_template"]
    assert after == before


def test_placeholder_does_not_block_a_legitimate_template(client, admin_token):
    """``{tracking_no}`` is substituted before parsing, not rejected."""
    r = _put(client, admin_token,
             {"api_url_template": "https://track.example.org/p/{tracking_no}?x=1"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["api_url_template"] == "https://track.example.org/p/{tracking_no}?x=1"


# ── Defect 3: mass assignment ────────────────────────────────────────────────

def test_id_cannot_be_assigned(app, client, admin_token):
    with app.app_context():
        from app.models.tracking_provider import TrackingProvider
        original_id = TrackingProvider.query.filter_by(code=CODE).first().id
    assert _put(client, admin_token, {"id": 9999}).status_code == 400
    with app.app_context():
        from app.models.tracking_provider import TrackingProvider
        assert TrackingProvider.query.filter_by(code=CODE).first().id == original_id
        assert TrackingProvider.query.filter_by(id=9999).first() is None


def test_unknown_key_is_rejected_by_name(client, admin_token):
    r = _put(client, admin_token, {"to_dict": "pwned"})
    assert r.status_code == 400
    assert "to_dict" in r.data.decode()


def test_encrypted_column_cannot_be_written_directly(client, admin_token):
    """Chosen ciphertext must not be settable through the API."""
    r = _put(client, admin_token, {"api_key_encrypted": "enc:v1:forged"})
    assert r.status_code == 400


# ── Defect 4: omitting a secret preserves it, "" clears it ───────────────────

def test_omitting_the_key_on_update_preserves_it(app, client, admin_token):
    r = _put(client, admin_token, {"name": "SecTest Courier Renamed"})
    assert r.status_code == 200
    with app.app_context():
        from app.models.tracking_provider import TrackingProvider
        assert TrackingProvider.query.filter_by(code=CODE).first().api_key == SECRET


def test_sending_a_new_key_replaces_it(app, client, admin_token):
    r = _put(client, admin_token, {"api_key": ROTATED})
    assert r.status_code == 200
    assert ROTATED not in r.data.decode()
    with app.app_context():
        from app.models.tracking_provider import TrackingProvider
        assert TrackingProvider.query.filter_by(code=CODE).first().api_key == ROTATED


def test_sending_an_empty_string_clears_the_key(app, client, admin_token):
    r = _put(client, admin_token, {"api_key": ""})
    assert r.status_code == 200
    assert r.get_json()["api_key_masked"] == ""
    with app.app_context():
        from app.models.tracking_provider import TrackingProvider
        assert TrackingProvider.query.filter_by(code=CODE).first().api_key_encrypted is None
