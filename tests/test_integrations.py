"""Integration surface: carrier connectors, merchant API keys, webhooks,
notification templates and claims.

These assert behaviour rather than shape. The ones worth reading twice are
the leak tests: a carrier credential must not appear in any response body,
and one merchant's key must not reach another merchant's rows. Both are
checked against the *raw* body, because a secret that escapes through a
field nobody thought to look at still escapes.
"""

from tests.conftest import register_and_login, auth_header

API_KEY_HEADER = "X-API-Key"


# ── helpers ──────────────────────────────────────────────────────────────────

def _create_carrier(client, admin_token, code, **overrides):
    payload = {
        "code": code,
        "name": f"Carrier {code}",
        "api_base_url": "https://api.example-carrier.com",
        "auth_type": "api_key",
        "tracking_url_template": "https://track.example-carrier.com/{tracking_no}",
    }
    payload.update(overrides)
    return client.post("/api/carriers/", json=payload, headers=auth_header(admin_token))


def _mint_key(client, token, name, scopes):
    r = client.post("/api/merchant/keys",
                    json={"name": name, "scopes": scopes},
                    headers=auth_header(token))
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _key_header(plaintext):
    return {API_KEY_HEADER: plaintext}


def _book(client, plaintext, reference_no, **overrides):
    payload = {
        "origin": "Kathmandu",
        "destination": "Pokhara",
        "reference_no": reference_no,
        "sender_name": "Shop",
        "receiver_name": "Sita",
        "receiver_phone": "9800000000",
        "total_weight_kg": 1.5,
        "total_packages": 1,
    }
    payload.update(overrides)
    return client.post("/api/merchant/shipments", json=payload, headers=_key_header(plaintext))


# ── carrier connectors ───────────────────────────────────────────────────────

def test_carrier_secret_never_returned_in_plaintext(client, admin_token):
    """A stored carrier credential must not appear in any response body."""
    secret = "sk-live-carrier-DO-NOT-LEAK-7f3a9c21"
    r = _create_carrier(client, admin_token, "leakcheck", api_key=secret, is_active=True)
    assert r.status_code == 201, r.get_json()
    assert secret not in r.get_data(as_text=True)

    # Admin listing with config and masked secrets.
    r = client.get("/api/carriers/all", headers=auth_header(admin_token))
    assert r.status_code == 200
    assert secret not in r.get_data(as_text=True)
    row = next(c for c in r.get_json() if c["code"] == "leakcheck")
    assert row["api_key_masked"].endswith("9c21")
    assert row["api_key_masked"].startswith("*")
    assert "api_key" not in row

    # Public listing and public detail.
    for url in ("/api/carriers/", "/api/carriers/leakcheck"):
        r = client.get(url)
        assert r.status_code == 200
        assert secret not in r.get_data(as_text=True)

    # And an update round-trip must not echo it back either.
    r = client.put("/api/carriers/leakcheck",
                   json={"display_name": "Leak Check"},
                   headers=auth_header(admin_token))
    assert r.status_code == 200
    assert secret not in r.get_data(as_text=True)


def test_carrier_secret_survives_update_that_omits_it(client, admin_token):
    """Editing other fields must not silently wipe the stored credential."""
    secret = "sk-live-keepme-4821"
    _create_carrier(client, admin_token, "keepsecret", api_key=secret)
    client.put("/api/carriers/keepsecret", json={"name": "Renamed"},
               headers=auth_header(admin_token))
    r = client.get("/api/carriers/all", headers=auth_header(admin_token))
    row = next(c for c in r.get_json() if c["code"] == "keepsecret")
    assert row["api_key_masked"].endswith("4821")
    assert row["is_configured"] is True


def test_carrier_loopback_and_private_urls_rejected(client, admin_token):
    """An admin-set URL we will later fetch is a stored SSRF if unchecked."""
    blocked = [
        "http://127.0.0.1:8080/api",
        "http://localhost/api",
        "http://10.0.0.5/api",
        "http://192.168.1.10/api",
        "http://172.16.4.4/api",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:9000/api",
        "http://internal-box.internal/api",
        "ftp://files.example.com/api",
        "file:///etc/passwd",
        "https://user:pass@api.example.com/",
    ]
    for i, url in enumerate(blocked):
        r = _create_carrier(client, admin_token, f"ssrf{i}", api_base_url=url)
        assert r.status_code == 400, f"{url} should have been rejected: {r.get_json()}"


def test_carrier_private_url_rejected_on_every_fetched_field(client, admin_token):
    """webhook_url and tracking_url_template get the same guard as api_base_url."""
    r = _create_carrier(client, admin_token, "ssrfhook",
                        webhook_url="http://127.0.0.1:5000/inbound")
    assert r.status_code == 400
    r = _create_carrier(client, admin_token, "ssrftrack",
                        tracking_url_template="http://192.168.0.9/t/{tracking_no}")
    assert r.status_code == 400
    # And on update, not just create.
    _create_carrier(client, admin_token, "ssrfupdate")
    r = client.put("/api/carriers/ssrfupdate",
                   json={"api_base_url": "http://127.0.0.1:1234/"},
                   headers=auth_header(admin_token))
    assert r.status_code == 400


def test_carrier_public_https_url_accepted(client, admin_token):
    r = _create_carrier(client, admin_token, "goodurl",
                        api_base_url="https://api.dhl.com/track",
                        webhook_url="https://moonlight.example.com/inbound/dhl")
    assert r.status_code == 201, r.get_json()


def test_carrier_rejects_unknown_auth_type(client, admin_token):
    r = _create_carrier(client, admin_token, "badauth", auth_type="telepathy")
    assert r.status_code == 400


def test_carrier_mass_assignment_is_refused(client, admin_token):
    """Health counters and the ciphertext column are not client-writable."""
    r = _create_carrier(client, admin_token, "noassign",
                        health_status="ok",
                        consecutive_failures=99,
                        api_key_encrypted="enc:v1:forged")
    assert r.status_code == 201
    body = r.get_json()
    assert body["health_status"] == "unknown"
    assert body["consecutive_failures"] in (0, None)
    assert body["api_key_masked"] == ""


def test_carrier_create_requires_super_admin(client):
    tok = register_and_login(client, "carrier_customer@test.com")
    r = _create_carrier(client, tok, "nope")
    assert r.status_code == 403


def test_carrier_test_endpoint_reports_gaps_without_network(client, admin_token):
    """POST /<code>/test audits configuration and makes no outbound call."""
    _create_carrier(client, admin_token, "halfdone",
                    supports_tracking=True, api_key="")
    r = client.post("/api/carriers/halfdone/test", headers=auth_header(admin_token))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["network_call_made"] is False
    assert body["live_dispatch"]["enabled"] is False
    assert body["ready"] is False
    joined = " ".join(body["missing"])
    assert "api_key" in joined
    assert "track_endpoint" in joined


def test_carrier_tracking_url(client, admin_token):
    _create_carrier(client, admin_token, "trackurl", is_active=True)
    r = client.get("/api/carriers/trackurl/tracking-url/ABC123")
    assert r.status_code == 200
    assert r.get_json()["tracking_url"] == "https://track.example-carrier.com/ABC123"


def test_carrier_deactivate_hides_from_public_list(client, admin_token):
    _create_carrier(client, admin_token, "goingaway", is_active=True)
    assert client.get("/api/carriers/goingaway").status_code == 200
    r = client.delete("/api/carriers/goingaway", headers=auth_header(admin_token))
    assert r.status_code == 200
    assert client.get("/api/carriers/goingaway").status_code == 404
    codes = [c["code"] for c in client.get("/api/carriers/").get_json()]
    assert "goingaway" not in codes


# ── merchant API keys ────────────────────────────────────────────────────────

def test_plaintext_key_returned_exactly_once(client):
    tok = register_and_login(client, "key_once@test.com")
    created = _mint_key(client, tok, "Storefront", ["shipments:read"])
    plaintext = created["api_key"]
    assert plaintext.startswith("mk_live_")
    assert "shown only once" in created["api_key_notice"]

    r = client.get("/api/merchant/keys", headers=auth_header(tok))
    assert r.status_code == 200
    raw = r.get_data(as_text=True)
    assert plaintext not in raw
    for row in r.get_json():
        assert "api_key" not in row
        assert "key_hash" not in row
        assert row["key_prefix"] in plaintext


def test_key_scopes_validated(client):
    tok = register_and_login(client, "key_scopes@test.com")
    r = client.post("/api/merchant/keys",
                    json={"name": "Bad", "scopes": ["shipments:nuke"]},
                    headers=auth_header(tok))
    assert r.status_code == 400
    r = client.post("/api/merchant/keys", json={"name": "Bad", "scopes": []},
                    headers=auth_header(tok))
    assert r.status_code == 400


def test_api_key_authorises_its_scope_and_refuses_others(client):
    tok = register_and_login(client, "key_scoped@test.com")
    tracking_only = _mint_key(client, tok, "Tracking only", ["tracking:read"])["api_key"]
    readable = _mint_key(client, tok, "Reader", ["shipments:read"])["api_key"]

    # The reader key may list shipments.
    r = client.get("/api/merchant/shipments", headers=_key_header(readable))
    assert r.status_code == 200, r.get_json()
    assert "shipments" in r.get_json()

    # The tracking-only key may not, and is told which scope is missing.
    r = client.get("/api/merchant/shipments", headers=_key_header(tracking_only))
    assert r.status_code == 403
    assert "shipments:read" in r.get_json()["msg"]

    # Nor may it write.
    r = _book(client, tracking_only, "ref-scope-1")
    assert r.status_code == 403


def test_missing_or_garbage_key_is_401(client):
    assert client.get("/api/merchant/shipments").status_code == 401
    assert client.get("/api/merchant/shipments",
                      headers=_key_header("not-a-key")).status_code == 401
    # An unknown prefix and a known prefix with the wrong body must be
    # indistinguishable from the outside.
    tok = register_and_login(client, "key_probe@test.com")
    real = _mint_key(client, tok, "Real", ["shipments:read"])
    forged = f"{real['key_prefix']}.totally-wrong-body"
    unknown = "mk_live_deadbeef.totally-wrong-body"
    a = client.get("/api/merchant/shipments", headers=_key_header(forged))
    b = client.get("/api/merchant/shipments", headers=_key_header(unknown))
    assert a.status_code == b.status_code == 401
    assert a.get_json() == b.get_json()


def test_revoked_key_is_rejected(client):
    tok = register_and_login(client, "key_revoke@test.com")
    created = _mint_key(client, tok, "Doomed", ["shipments:read"])
    plaintext = created["api_key"]
    assert client.get("/api/merchant/shipments",
                      headers=_key_header(plaintext)).status_code == 200

    r = client.delete(f"/api/merchant/keys/{created['id']}", headers=auth_header(tok))
    assert r.status_code == 200
    assert r.get_json()["key"]["is_usable"] is False

    r = client.get("/api/merchant/shipments", headers=_key_header(plaintext))
    assert r.status_code == 401


def test_cannot_revoke_another_merchants_key(client):
    owner = register_and_login(client, "key_owner@test.com")
    other = register_and_login(client, "key_other@test.com")
    created = _mint_key(client, owner, "Mine", ["shipments:read"])
    r = client.delete(f"/api/merchant/keys/{created['id']}", headers=auth_header(other))
    assert r.status_code == 404


def test_key_records_use(client):
    tok = register_and_login(client, "key_usage@test.com")
    created = _mint_key(client, tok, "Counter", ["shipments:read"])
    client.get("/api/merchant/shipments", headers=_key_header(created["api_key"]))
    row = next(k for k in client.get("/api/merchant/keys",
                                     headers=auth_header(tok)).get_json()
               if k["id"] == created["id"])
    assert row["use_count"] >= 1
    assert row["last_used_at"] is not None


# ── merchant data isolation ──────────────────────────────────────────────────

def test_one_merchants_key_cannot_read_anothers_shipments(client):
    alice = register_and_login(client, "merchant_alice@test.com")
    bob = register_and_login(client, "merchant_bob@test.com")
    alice_key = _mint_key(client, alice, "Alice",
                          ["shipments:read", "shipments:write", "tracking:read"])["api_key"]
    bob_key = _mint_key(client, bob, "Bob",
                        ["shipments:read", "shipments:write", "tracking:read"])["api_key"]

    r = _book(client, alice_key, "alice-order-1")
    assert r.status_code == 201, r.get_json()
    alice_shipment = r.get_json()
    tracking_no = alice_shipment["tracking_no"]

    # Bob's listing never contains Alice's parcel.
    r = client.get("/api/merchant/shipments", headers=_key_header(bob_key))
    assert r.status_code == 200
    assert tracking_no not in r.get_data(as_text=True)

    # Nor can he reach it by reference or by tracking number.
    assert client.get("/api/merchant/shipments/alice-order-1",
                      headers=_key_header(bob_key)).status_code == 404
    assert client.get(f"/api/merchant/track/{tracking_no}",
                      headers=_key_header(bob_key)).status_code == 404

    # Alice can, through both doors.
    assert client.get("/api/merchant/shipments/alice-order-1",
                      headers=_key_header(alice_key)).status_code == 200
    r = client.get(f"/api/merchant/track/{tracking_no}", headers=_key_header(alice_key))
    assert r.status_code == 200
    assert r.get_json()["reference_no"] == "alice-order-1"


def test_duplicate_reference_no_is_refused(client):
    tok = register_and_login(client, "merchant_dupe@test.com")
    key = _mint_key(client, tok, "Dupe", ["shipments:read", "shipments:write"])["api_key"]
    assert _book(client, key, "dupe-order-1").status_code == 201
    r = _book(client, key, "dupe-order-1")
    assert r.status_code == 409


def test_booking_does_not_accept_client_set_charges(client):
    """Pricing is server-side; a merchant cannot book itself a free parcel."""
    tok = register_and_login(client, "merchant_price@test.com")
    key = _mint_key(client, tok, "Pricer", ["shipments:read", "shipments:write"])["api_key"]
    r = _book(client, key, "price-order-1",
              total_charge=0.01, status="delivered", freight_charge=0.0, user_id=1)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["status"] == "pending"
    assert body["total_charge"] in (0, 0.0)
    assert body["merchant_id"] == body["user_id"]


# ── webhooks ─────────────────────────────────────────────────────────────────

_DEFAULT_EVENTS = ["shipment.delivered"]


def _create_endpoint(client, token, url="https://hooks.example.com/moonlight",
                     events=_DEFAULT_EVENTS):
    return client.post("/api/webhooks/",
                       json={"url": url, "events": events},
                       headers=auth_header(token))


def test_webhook_secret_returned_once_then_masked(client):
    tok = register_and_login(client, "hook_secret@test.com")
    r = _create_endpoint(client, tok)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    secret = body["secret"]
    assert secret.startswith("whsec_")
    assert "only once" in body["secret_notice"]

    r = client.get("/api/webhooks/", headers=auth_header(tok))
    assert r.status_code == 200
    assert secret not in r.get_data(as_text=True)
    row = next(e for e in r.get_json() if e["id"] == body["id"])
    assert "secret" not in row
    assert row["secret_masked"].startswith("*")


def test_webhook_url_validated_like_carrier_urls(client):
    tok = register_and_login(client, "hook_ssrf@test.com")
    for url in ("http://127.0.0.1:9000/hook", "http://10.1.2.3/hook", "notaurl"):
        assert _create_endpoint(client, tok, url=url).status_code == 400


def test_webhook_events_validated(client):
    tok = register_and_login(client, "hook_events@test.com")
    r = _create_endpoint(client, tok, events=["shipment.teleported"])
    assert r.status_code == 400
    r = _create_endpoint(client, tok, events=[])
    assert r.status_code == 400


def test_webhook_test_enqueues_pending_delivery(client):
    tok = register_and_login(client, "hook_test@test.com")
    endpoint = _create_endpoint(client, tok).get_json()

    r = client.post(f"/api/webhooks/{endpoint['id']}/test", headers=auth_header(tok))
    assert r.status_code == 202, r.get_json()
    body = r.get_json()
    assert body["sending_enabled"] is False
    assert body["delivery"]["status"] == "pending"
    assert body["delivery"]["attempt_count"] == 0

    r = client.get(f"/api/webhooks/{endpoint['id']}/deliveries", headers=auth_header(tok))
    assert r.status_code == 200
    deliveries = r.get_json()["deliveries"]
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "pending"
    assert deliveries[0]["event_type"] == "webhook.test"
    # Nothing was actually sent.
    assert deliveries[0]["delivered_at"] is None
    assert deliveries[0]["response_status"] is None


def test_booking_enqueues_shipment_created_for_subscribed_endpoint(client):
    """The internal enqueue helper is wired into a real caller."""
    tok = register_and_login(client, "hook_booking@test.com")
    endpoint = _create_endpoint(client, tok, events=["shipment.created"]).get_json()
    key = _mint_key(client, tok, "Booker", ["shipments:write"])["api_key"]

    r = _book(client, key, "hook-order-1")
    assert r.status_code == 201, r.get_json()

    r = client.get(f"/api/webhooks/{endpoint['id']}/deliveries", headers=auth_header(tok))
    deliveries = r.get_json()["deliveries"]
    assert any(d["event_type"] == "shipment.created" and d["status"] == "pending"
               for d in deliveries)


def test_webhook_endpoints_are_per_merchant(client):
    owner = register_and_login(client, "hook_owner@test.com")
    other = register_and_login(client, "hook_intruder@test.com")
    endpoint = _create_endpoint(client, owner).get_json()

    for call in (
        lambda: client.get(f"/api/webhooks/{endpoint['id']}", headers=auth_header(other)),
        lambda: client.post(f"/api/webhooks/{endpoint['id']}/test", headers=auth_header(other)),
        lambda: client.get(f"/api/webhooks/{endpoint['id']}/deliveries",
                           headers=auth_header(other)),
        lambda: client.delete(f"/api/webhooks/{endpoint['id']}", headers=auth_header(other)),
    ):
        assert call().status_code == 404

    assert client.get("/api/webhooks/", headers=auth_header(other)).get_json() == []


# ── notification templates ───────────────────────────────────────────────────

NEPALI_BODY = "तपाईंको पार्सल {tracking_no} डेलिभरीको लागि निस्कियो।"


def _create_template(client, admin_token, code, **overrides):
    payload = {
        "code": code,
        "name": "Out for delivery",
        "event": "shipment.out_for_delivery",
        "channel": "sms",
        "subject": "Parcel {tracking_no}",
        "body_en": "Your parcel {tracking_no} is out for delivery.",
        "body_ne": NEPALI_BODY,
        "variables": ["tracking_no"],
    }
    payload.update(overrides)
    return client.post("/api/notifications/templates", json=payload,
                       headers=auth_header(admin_token))


def test_template_renders_nepali_when_asked(client, admin_token):
    assert _create_template(client, admin_token, "ofd_ne").status_code == 201

    r = client.post("/api/notifications/templates/ofd_ne/preview",
                    json={"context": {"tracking_no": "MNL123"}, "language": "ne"},
                    headers=auth_header(admin_token))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["language"] == "ne"
    assert "डेलिभरीको" in body["body"]
    assert "MNL123" in body["body"]
    assert "out for delivery" not in body["body"]
    assert body["unfilled_variables"] == []
    assert body["sent"] is False

    # English remains the default.
    r = client.post("/api/notifications/templates/ofd_ne/preview",
                    json={"context": {"tracking_no": "MNL123"}},
                    headers=auth_header(admin_token))
    assert "out for delivery" in r.get_json()["body"]


def test_template_preview_reports_unfilled_placeholders(client, admin_token):
    _create_template(client, admin_token, "ofd_unfilled")
    r = client.post("/api/notifications/templates/ofd_unfilled/preview",
                    json={"context": {}}, headers=auth_header(admin_token))
    assert r.get_json()["unfilled_variables"] == ["tracking_no"]


def test_template_event_and_channel_validated(client, admin_token):
    assert _create_template(client, admin_token, "bad_evt",
                            event="shipment.abducted").status_code == 400
    assert _create_template(client, admin_token, "bad_chan",
                            channel="carrier_pigeon").status_code == 400


def test_templates_are_admin_only(client):
    tok = register_and_login(client, "tpl_customer@test.com")
    assert client.get("/api/notifications/templates",
                      headers=auth_header(tok)).status_code == 403
    assert _create_template(client, tok, "sneaky").status_code == 403


def test_sms_provider_credentials_masked(client, admin_token):
    token_value = "sparrow-token-ABCD-9931"
    r = client.post("/api/notifications/providers", json={
        "code": "sparrow",
        "name": "Sparrow SMS",
        "api_base_url": "https://api.sparrowsms.com",
        "send_endpoint": "/v2/sms",
        "token": token_value,
        "sender_id": "Moonlight",
        "param_map": {"to": "to", "text": "text"},
    }, headers=auth_header(admin_token))
    assert r.status_code == 201, r.get_json()
    assert token_value not in r.get_data(as_text=True)
    assert r.get_json()["token_masked"].endswith("9931")

    r = client.get("/api/notifications/providers", headers=auth_header(admin_token))
    assert token_value not in r.get_data(as_text=True)


def test_sms_provider_url_validated(client, admin_token):
    r = client.post("/api/notifications/providers", json={
        "code": "localsms", "name": "Local", "api_base_url": "http://127.0.0.1:8000",
    }, headers=auth_header(admin_token))
    assert r.status_code == 400


def test_notification_logs_paginated_and_admin_only(client, admin_token):
    r = client.get("/api/notifications/logs?page=1&per_page=5",
                   headers=auth_header(admin_token))
    assert r.status_code == 200
    body = r.get_json()
    for key in ("logs", "total", "page", "pages", "per_page"):
        assert key in body
    assert body["per_page"] == 5

    tok = register_and_login(client, "logs_customer@test.com")
    assert client.get("/api/notifications/logs",
                      headers=auth_header(tok)).status_code == 403


# ── claims ───────────────────────────────────────────────────────────────────

def _customer_shipment(client, token, destination="Biratnagar"):
    r = client.post("/api/shipments/", json={
        "origin": "Kathmandu", "destination": destination,
        "receiver_name": "Hari", "total_weight_kg": 2.0, "total_packages": 1,
    }, headers=auth_header(token))
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _raise_claim(client, token, shipment_id, amount=5000.0):
    return client.post("/api/claims/", json={
        "shipment_id": shipment_id,
        "claim_type": "damaged",
        "description": "Box arrived crushed.",
        "claimed_amount": amount,
    }, headers=auth_header(token))


def test_customer_raises_and_lists_own_claim(client):
    tok = register_and_login(client, "claim_owner@test.com")
    shipment = _customer_shipment(client, tok)
    r = _raise_claim(client, tok, shipment["id"])
    assert r.status_code == 201, r.get_json()
    claim = r.get_json()
    assert claim["status"] == "open"
    assert claim["approved_amount"] == 0.0
    assert claim["claim_no"].startswith("CLM-")

    r = client.get("/api/claims/mine", headers=auth_header(tok))
    assert r.status_code == 200
    assert any(c["id"] == claim["id"] for c in r.get_json()["claims"])


def test_customer_cannot_approve_their_own_claim(client, admin_token):
    """The claimant states the amount; only an administrator decides it."""
    tok = register_and_login(client, "claim_approver@test.com")
    shipment = _customer_shipment(client, tok)
    claim = _raise_claim(client, tok, shipment["id"]).get_json()

    r = client.post(f"/api/claims/{claim['id']}/approve",
                    json={"approved_amount": 5000.0}, headers=auth_header(tok))
    assert r.status_code == 403

    # Nor by the back door of a plain update.
    r = client.put(f"/api/claims/{claim['id']}",
                   json={"approved_amount": 5000.0}, headers=auth_header(tok))
    assert r.status_code == 403
    r = client.put(f"/api/claims/{claim['id']}",
                   json={"status": "approved"}, headers=auth_header(tok))
    assert r.status_code == 403

    # Untouched.
    r = client.get(f"/api/claims/{claim['id']}", headers=auth_header(tok))
    assert r.get_json()["approved_amount"] == 0.0
    assert r.get_json()["status"] == "open"

    # The admin can.
    r = client.post(f"/api/claims/{claim['id']}/approve",
                    json={"approved_amount": 4000.0}, headers=auth_header(admin_token))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["status"] == "approved"
    assert r.get_json()["approved_amount"] == 4000.0


def test_claim_cannot_be_raised_against_someone_elses_shipment(client):
    owner = register_and_login(client, "claim_shipowner@test.com")
    stranger = register_and_login(client, "claim_stranger@test.com")
    shipment = _customer_shipment(client, owner)
    r = _raise_claim(client, stranger, shipment["id"])
    assert r.status_code == 403


def test_closed_claim_is_immutable(client, admin_token):
    tok = register_and_login(client, "claim_closed@test.com")
    shipment = _customer_shipment(client, tok)
    claim = _raise_claim(client, tok, shipment["id"]).get_json()

    r = client.post(f"/api/claims/{claim['id']}/approve",
                    json={"approved_amount": 1000.0}, headers=auth_header(admin_token))
    assert r.status_code == 200
    r = client.post(f"/api/claims/{claim['id']}/settle", headers=auth_header(admin_token),
                    json={"resolution_note": "Paid out by bank transfer."})
    assert r.status_code == 200
    assert r.get_json()["status"] == "settled"
    assert r.get_json()["is_closed"] is True

    # Every write path now refuses.
    assert client.post(f"/api/claims/{claim['id']}/approve", json={"approved_amount": 9999.0},
                       headers=auth_header(admin_token)).status_code == 409
    assert client.post(f"/api/claims/{claim['id']}/reject", json={"rejection_reason": "no"},
                       headers=auth_header(admin_token)).status_code == 409
    assert client.post(f"/api/claims/{claim['id']}/status", json={"status": "open"},
                       headers=auth_header(admin_token)).status_code == 409
    assert client.put(f"/api/claims/{claim['id']}", json={"description": "changed"},
                      headers=auth_header(tok)).status_code == 409
    assert client.post(f"/api/claims/{claim['id']}/withdraw",
                       headers=auth_header(tok)).status_code == 409

    r = client.get(f"/api/claims/{claim['id']}", headers=auth_header(admin_token))
    assert r.get_json()["approved_amount"] == 1000.0


def test_settle_requires_approval_first(client, admin_token):
    tok = register_and_login(client, "claim_settle@test.com")
    shipment = _customer_shipment(client, tok)
    claim = _raise_claim(client, tok, shipment["id"]).get_json()
    r = client.post(f"/api/claims/{claim['id']}/settle", headers=auth_header(admin_token),
                    json={})
    assert r.status_code == 409


def test_approved_amount_cannot_exceed_claim(client, admin_token):
    tok = register_and_login(client, "claim_excess@test.com")
    shipment = _customer_shipment(client, tok)
    claim = _raise_claim(client, tok, shipment["id"], amount=1000.0).get_json()
    r = client.post(f"/api/claims/{claim['id']}/approve",
                    json={"approved_amount": 50000.0}, headers=auth_header(admin_token))
    assert r.status_code == 400


def test_admin_assigns_and_moves_claim_status(client, admin_token):
    tok = register_and_login(client, "claim_assign@test.com")
    shipment = _customer_shipment(client, tok)
    claim = _raise_claim(client, tok, shipment["id"]).get_json()

    me = client.get("/api/auth/me", headers=auth_header(admin_token))
    admin_id = me.get_json().get("id") if me.status_code == 200 else None
    if admin_id:
        r = client.post(f"/api/claims/{claim['id']}/assign",
                        json={"assigned_to": admin_id}, headers=auth_header(admin_token))
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["assigned_to"] == admin_id
        assert r.get_json()["status"] == "under_review"

    r = client.post(f"/api/claims/{claim['id']}/status",
                    json={"status": "awaiting_customer"}, headers=auth_header(admin_token))
    assert r.status_code == 200
    assert r.get_json()["status"] == "awaiting_customer"

    # /status may not be used to close a claim.
    r = client.post(f"/api/claims/{claim['id']}/status",
                    json={"status": "settled"}, headers=auth_header(admin_token))
    assert r.status_code == 400


def test_claim_queue_is_admin_only_and_filterable(client, admin_token):
    tok = register_and_login(client, "claim_queue@test.com")
    assert client.get("/api/claims/", headers=auth_header(tok)).status_code == 403

    r = client.get("/api/claims/?status=open&per_page=5", headers=auth_header(admin_token))
    assert r.status_code == 200
    body = r.get_json()
    assert all(c["status"] == "open" for c in body["claims"])
    assert body["per_page"] == 5

    r = client.get("/api/claims/?status=exploded", headers=auth_header(admin_token))
    assert r.status_code == 400


def test_customer_can_withdraw_own_claim(client):
    tok = register_and_login(client, "claim_withdraw@test.com")
    other = register_and_login(client, "claim_withdraw_other@test.com")
    shipment = _customer_shipment(client, tok)
    claim = _raise_claim(client, tok, shipment["id"]).get_json()

    assert client.post(f"/api/claims/{claim['id']}/withdraw",
                       headers=auth_header(other)).status_code in (403, 404)
    r = client.post(f"/api/claims/{claim['id']}/withdraw", headers=auth_header(tok))
    assert r.status_code == 200
    assert r.get_json()["status"] == "withdrawn"
    assert r.get_json()["is_closed"] is True


def test_stranger_cannot_read_a_claim(client):
    owner = register_and_login(client, "claim_read_owner@test.com")
    stranger = register_and_login(client, "claim_read_stranger@test.com")
    shipment = _customer_shipment(client, owner)
    claim = _raise_claim(client, owner, shipment["id"]).get_json()
    r = client.get(f"/api/claims/{claim['id']}", headers=auth_header(stranger))
    assert r.status_code == 403
