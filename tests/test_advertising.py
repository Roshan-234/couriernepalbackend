"""Advertising, promotions and site notices.

These cover the properties the module exists to guarantee rather than the
shape of a 200: that a draft campaign is invisible, that targeting is page
and country only and the country cannot be named by the caller, that an
admin-editable field which ends up in an ``href`` cannot hold
``javascript:``, that an image without alt text cannot be published, that
the public counter endpoints cannot be inflated by one caller in a loop,
and that the seed never puts an advert on a live site.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.advertising import throttle
from app.advertising.routes import ads_ns
from app.extensions import api, db
from app.models.advertising import AdCampaign, AdCreative, AdPlacement, SiteNotice
from tests.conftest import auth_header, register_and_login

# ``app/swagger_setup.py`` is owned by another workstream this week, so the
# namespace is not mounted in the application yet. Mounting it here at
# collection time — before any test has built the app — registers it exactly
# as swagger_setup will; the guard makes this a no-op once that lands.
if not any(ns.name == "ads" for ns in api.namespaces):
    api.add_namespace(ads_ns, path="/api/ads")

BASE = "/api/ads"
PAST = (datetime.utcnow() - timedelta(days=2)).isoformat()
FUTURE = (datetime.utcnow() + timedelta(days=30)).isoformat()


def _tag():
    """The test database is session-scoped and shared, so codes must be unique."""
    return uuid4().hex[:8]


@pytest.fixture
def admin(admin_token):
    return auth_header(admin_token)


@pytest.fixture
def customer(app):
    """A signed-in account with no admin role, as a bearer header.

    Registered on a throwaway client on purpose: ``/api/auth/login`` also
    sets a JWT cookie, and the shared session client is reused by every
    other test module — leaving a customer session in its cookie jar turns
    a later module's "anonymous" request into an authenticated one.
    """
    return auth_header(register_and_login(app.test_client(),
                                          f"ads-{_tag()}@example.test"))


@pytest.fixture
def public(app):
    """A fresh cookie jar, so 'a visitor' really is an anonymous one.

    The shared session client picks up a consent cookie from other modules'
    tests, which would change what ``subject_key()`` returns underneath the
    dismissal and throttle behaviour.
    """
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_throttle():
    """Counter defences are process-wide state; no test may inherit another's."""
    throttle.reset()
    yield
    throttle.reset()


# ── builders ─────────────────────────────────────────────────────────────────

def _placement(client, admin, **overrides):
    payload = {"code": f"slot_{_tag()}", "name": "Test slot", "max_creatives": 1}
    payload.update(overrides)
    result = client.post(f"{BASE}/placements", headers=admin, json=payload)
    assert result.status_code == 201, result.get_json()
    return result.get_json()


def _campaign(client, admin, **overrides):
    payload = {"code": f"CAMP_{_tag()}", "name": "Test campaign"}
    payload.update(overrides)
    result = client.post(f"{BASE}/campaigns", headers=admin, json=payload)
    assert result.status_code == 201, result.get_json()
    return result.get_json()


def _creative(client, admin, campaign_id, placement_id, **overrides):
    payload = {"placement_id": placement_id, "headline": "Dashain cargo offer"}
    payload.update(overrides)
    result = client.post(f"{BASE}/campaigns/{campaign_id}/creatives",
                         headers=admin, json=payload)
    return result


def _make_creative(client, admin, campaign_id, placement_id, **overrides):
    result = _creative(client, admin, campaign_id, placement_id, **overrides)
    assert result.status_code == 201, result.get_json()
    return result.get_json()


def _activate(client, admin, campaign_id):
    result = client.post(f"{BASE}/campaigns/{campaign_id}/activate", headers=admin)
    assert result.status_code == 200, result.get_json()
    return result.get_json()


def _live_campaign(client, admin, **overrides):
    """A campaign that is running now, with a window around today."""
    payload = {"starts_at": PAST, "ends_at": FUTURE}
    payload.update(overrides)
    campaign = _campaign(client, admin, **payload)
    _activate(client, admin, campaign["id"])
    return campaign


def _served(public, path="/", placement=None, language=None, headers=None):
    query = f"?path={path}"
    if placement:
        query += f"&placement={placement}"
    if language:
        query += f"&language={language}"
    result = public.get(f"{BASE}/serve{query}", headers=headers or {})
    assert result.status_code == 200, result.get_json()
    return result.get_json()["items"]


# ── Serving: campaign state ──────────────────────────────────────────────────

def test_serve_returns_nothing_for_a_draft_campaign(client, admin, public):
    """A draft is work in progress. It must never reach a visitor."""
    slot = _placement(client, admin)
    campaign = _campaign(client, admin)
    _make_creative(client, admin, campaign["id"], slot["id"])

    assert campaign["status"] == "draft"
    assert _served(public, placement=slot["code"]) == []


def test_serve_returns_the_creative_once_activated_and_dated_live(client, admin, public):
    """Running, inside its window, active creative: now it renders."""
    slot = _placement(client, admin)
    campaign = _live_campaign(client, admin)
    creative = _make_creative(client, admin, campaign["id"], slot["id"],
                              headline="Send Dashain home", call_to_action="Get a quote")

    items = _served(public, placement=slot["code"])
    assert [i["id"] for i in items] == [creative["id"]]
    assert items[0]["headline"] == "Send Dashain home"


def test_serve_stops_when_the_campaign_is_paused_or_finished(client, admin, public):
    slot = _placement(client, admin)
    campaign = _live_campaign(client, admin)
    _make_creative(client, admin, campaign["id"], slot["id"])
    assert len(_served(public, placement=slot["code"])) == 1

    client.post(f"{BASE}/campaigns/{campaign['id']}/pause", headers=admin)
    assert _served(public, placement=slot["code"]) == []


def test_serve_respects_the_campaign_date_window(client, admin, public):
    """A campaign activated before its start date is scheduled, not running."""
    slot = _placement(client, admin)
    campaign = _campaign(client, admin,
                         starts_at=(datetime.utcnow() + timedelta(days=5)).isoformat(),
                         ends_at=FUTURE)
    _make_creative(client, admin, campaign["id"], slot["id"])
    activated = _activate(client, admin, campaign["id"])

    assert activated["status"] == "scheduled"
    assert activated["is_live"] is False
    assert _served(public, placement=slot["code"]) == []


def test_serve_skips_a_deactivated_creative(client, admin, public):
    slot = _placement(client, admin)
    campaign = _live_campaign(client, admin)
    creative = _make_creative(client, admin, campaign["id"], slot["id"])
    assert len(_served(public, placement=slot["code"])) == 1

    client.post(f"{BASE}/creatives/{creative['id']}/deactivate", headers=admin)
    assert _served(public, placement=slot["code"]) == []


# ── Serving: ordering, capping and targeting ─────────────────────────────────

def test_serve_orders_by_priority_and_caps_at_max_creatives(client, admin, public):
    """Three creatives, a slot that holds two: the two best win, in order."""
    slot = _placement(client, admin, max_creatives=2)
    campaign = _live_campaign(client, admin)
    low = _make_creative(client, admin, campaign["id"], slot["id"],
                         headline="Low", priority=1)
    high = _make_creative(client, admin, campaign["id"], slot["id"],
                          headline="High", priority=99)
    middle = _make_creative(client, admin, campaign["id"], slot["id"],
                            headline="Middle", priority=50)

    items = _served(public, placement=slot["code"])
    assert [i["id"] for i in items] == [high["id"], middle["id"]]
    assert low["id"] not in [i["id"] for i in items]


def test_serve_filters_by_page_path(client, admin, public):
    """page_paths is a prefix match; empty means anywhere."""
    slot = _placement(client, admin, max_creatives=5)
    campaign = _live_campaign(client, admin)
    pricing_only = _make_creative(client, admin, campaign["id"], slot["id"],
                                  headline="Pricing only", page_paths=["/pricing"],
                                  priority=10)
    anywhere = _make_creative(client, admin, campaign["id"], slot["id"],
                              headline="Anywhere", priority=5)

    on_pricing = [i["id"] for i in _served(public, path="/pricing", placement=slot["code"])]
    on_home = [i["id"] for i in _served(public, path="/", placement=slot["code"])]

    assert on_pricing == [pricing_only["id"], anywhere["id"]]
    assert on_home == [anywhere["id"]]


def test_serve_filters_by_country_from_the_edge_header(client, admin, public):
    slot = _placement(client, admin, max_creatives=5)
    campaign = _live_campaign(client, admin)
    gulf = _make_creative(client, admin, campaign["id"], slot["id"],
                          headline="Gulf corridor", countries=["AE", "QA"], priority=10)
    everywhere = _make_creative(client, admin, campaign["id"], slot["id"],
                                headline="Everywhere", priority=5)

    in_dubai = [i["id"] for i in _served(public, placement=slot["code"],
                                         headers={"CF-IPCountry": "AE"})]
    in_london = [i["id"] for i in _served(public, placement=slot["code"],
                                          headers={"CF-IPCountry": "GB"})]

    assert in_dubai == [gulf["id"], everywhere["id"]]
    assert in_london == [everywhere["id"]]


def test_an_unknown_country_falls_open_rather_than_hiding_everything(client, admin, public):
    """No CDN header means no country, and ``matches()`` then does not filter.

    That is the model's deliberate choice and this test pins it so a future
    edit has to mean it. Behind a CDN the header is always there; without
    one — local development, a direct origin hit — failing closed would make
    every geo-targeted advert silently vanish and look like a broken module
    rather than a policy. Nothing is at risk either way, because the only
    thing geography decides is which offer is shown.
    """
    slot = _placement(client, admin)
    campaign = _live_campaign(client, admin)
    gulf = _make_creative(client, admin, campaign["id"], slot["id"],
                          headline="Gulf corridor", countries=["AE", "QA"])

    assert [i["id"] for i in _served(public, placement=slot["code"])] == [gulf["id"]]


def test_a_caller_cannot_name_their_own_country(client, admin, public):
    """Country comes from the edge. A query parameter must not override it.

    Otherwise anybody could enumerate what every region is being shown. The
    visitor here is in London and claims to be in Dubai; the Gulf creative
    stays hidden.
    """
    slot = _placement(client, admin)
    campaign = _live_campaign(client, admin)
    _make_creative(client, admin, campaign["id"], slot["id"],
                   headline="Gulf only", countries=["AE"])

    result = public.get(f"{BASE}/serve?path=/&placement={slot['code']}&country=AE",
                        headers={"CF-IPCountry": "GB"})
    assert result.status_code == 200
    assert result.get_json()["items"] == []


def test_serve_localises_to_nepali_and_falls_back_to_english(client, admin, public):
    slot = _placement(client, admin, max_creatives=5)
    campaign = _live_campaign(client, admin)
    _make_creative(client, admin, campaign["id"], slot["id"],
                   headline="Send Dashain home", headline_ne="दशैंमा घरमा उपहार पठाउनुहोस्",
                   body="English body only", priority=10)

    item = _served(public, placement=slot["code"], language="ne")[0]
    assert item["headline"] == "दशैंमा घरमा उपहार पठाउनुहोस्"
    # No Nepali body was written, so the English one stands rather than a blank.
    assert item["body"] == "English body only"


def test_serve_is_not_shared_cacheable(client, admin, public):
    """The answer depends on a header no cache keys on."""
    slot = _placement(client, admin)
    _live_campaign(client, admin)
    result = public.get(f"{BASE}/serve?path=/&placement={slot['code']}")
    assert "no-store" in result.headers.get("Cache-Control", "")


# ── Alt text ─────────────────────────────────────────────────────────────────

def test_a_creative_with_an_image_and_no_alt_text_cannot_be_activated(client, admin):
    """The image carries the offer, so it is content. Three ways in, all shut."""
    slot = _placement(client, admin)
    campaign = _campaign(client, admin)

    # 1. Created active with an image and no alt.
    refused = _creative(client, admin, campaign["id"], slot["id"],
                        image_path="/static/ads/dashain.png")
    assert refused.status_code == 400
    assert "image_alt" in refused.get_json()["message"]

    # 2. Created inactive, then switched on by PUT.
    creative = _make_creative(client, admin, campaign["id"], slot["id"],
                              image_path="/static/ads/dashain.png", is_active=False)
    by_put = client.put(f"{BASE}/creatives/{creative['id']}", headers=admin,
                        json={"is_active": True})
    assert by_put.status_code == 400

    # 3. Switched on by the activate endpoint.
    by_action = client.post(f"{BASE}/creatives/{creative['id']}/activate", headers=admin)
    assert by_action.status_code == 400
    assert db.session.get(AdCreative, creative["id"]).is_active is False

    # With alt text it activates.
    client.put(f"{BASE}/creatives/{creative['id']}", headers=admin,
               json={"image_alt": "Dashain gift parcels being loaded for Kathmandu"})
    allowed = client.post(f"{BASE}/creatives/{creative['id']}/activate", headers=admin)
    assert allowed.status_code == 200
    assert allowed.get_json()["is_active"] is True


def test_a_creative_without_an_image_needs_no_alt_text(client, admin):
    """The rule is about images, not about every creative."""
    slot = _placement(client, admin)
    campaign = _campaign(client, admin)
    creative = _make_creative(client, admin, campaign["id"], slot["id"])
    assert creative["is_active"] is True


# ── Path validation ──────────────────────────────────────────────────────────

HOSTILE_PATHS = [
    "javascript:alert(document.cookie)",
    "JavaScript:alert(1)",
    "https://moonlight-freight.example.evil/offer",
    "http://evil.example",
    "//evil.example/offer",
    "/\\evil.example/offer",
    "data:text/html;base64,PHNjcmlwdD4=",
    "pricing",
    "../admin",
]


@pytest.mark.parametrize("hostile", HOSTILE_PATHS)
def test_target_path_rejects_anything_that_is_not_a_site_path(client, admin, hostile):
    """target_path ends up in an href: javascript: is XSS, absolute is redirect."""
    slot = _placement(client, admin)
    campaign = _campaign(client, admin)
    result = _creative(client, admin, campaign["id"], slot["id"], target_path=hostile)
    assert result.status_code == 400, f"{hostile!r} was accepted"
    assert "target_path" in result.get_json()["message"]


@pytest.mark.parametrize("hostile", ["javascript:alert(1)", "https://evil.example"])
def test_campaign_landing_path_and_notice_link_path_are_validated_too(client, admin, hostile):
    """Same field, same risk, three different tables."""
    campaign = client.post(f"{BASE}/campaigns", headers=admin, json={
        "code": f"CAMP_{_tag()}", "name": "Bad landing", "landing_path": hostile})
    assert campaign.status_code == 400
    assert "landing_path" in campaign.get_json()["message"]

    notice = client.post(f"{BASE}/admin/notices", headers=admin, json={
        "code": f"NOTE_{_tag()}", "title": "Bad link", "link_path": hostile})
    assert notice.status_code == 400
    assert "link_path" in notice.get_json()["message"]


@pytest.mark.parametrize("good", ["/pricing", "/blog/dashain-cut-off", "/blog*", "/"])
def test_genuine_site_paths_are_accepted(client, admin, good):
    slot = _placement(client, admin)
    campaign = _campaign(client, admin)
    creative = _make_creative(client, admin, campaign["id"], slot["id"], target_path=good)
    assert creative["target_path"] == good


def test_page_paths_entries_are_validated_as_paths(client, admin):
    slot = _placement(client, admin)
    campaign = _campaign(client, admin)
    result = _creative(client, admin, campaign["id"], slot["id"],
                       page_paths=["/pricing", "https://evil.example"])
    assert result.status_code == 400


# ── Counters ─────────────────────────────────────────────────────────────────

def _live_creative(client, admin):
    slot = _placement(client, admin)
    campaign = _live_campaign(client, admin)
    return _make_creative(client, admin, campaign["id"], slot["id"]), campaign


def test_impression_and_click_counters_increment_and_ctr_computes(client, admin, public):
    creative, campaign = _live_creative(client, admin)
    cid = creative["id"]

    assert public.post(f"{BASE}/impression", json={"creative_id": cid}).status_code == 204
    throttle.reset()  # a second visit, rather than the same page view twice
    assert public.post(f"{BASE}/impression", json={"creative_id": cid}).status_code == 204
    assert public.post(f"{BASE}/click", json={"creative_id": cid}).status_code == 204

    row = db.session.get(AdCreative, cid)
    assert (row.impressions, row.clicks) == (2, 1)
    assert row.click_through_rate == 50.0

    report = client.get(f"{BASE}/report", headers=admin)
    assert report.status_code == 200
    entry = next(r for r in report.get_json()["items"] if r["campaign_id"] == campaign["id"])
    assert entry["impressions"] == 2
    assert entry["clicks"] == 1
    assert entry["click_through_rate"] == 50.0
    assert entry["creatives"][0]["creative_id"] == cid


def test_an_impression_returns_no_body(client, admin, public):
    creative, _ = _live_creative(client, admin)
    result = public.post(f"{BASE}/impression", json={"creative_id": creative["id"]})
    assert result.status_code == 204
    assert result.data == b""


def test_the_same_visitor_viewing_twice_counts_once(client, admin, public):
    """An impression means 'a visitor saw this', not 'it rendered'."""
    creative, _ = _live_creative(client, admin)
    for _ in range(5):
        public.post(f"{BASE}/impression", json={"creative_id": creative["id"]})
    assert db.session.get(AdCreative, creative["id"]).impressions == 1


def test_clicks_are_not_deduplicated(client, admin, public):
    """A visitor who clicks twice has clicked twice."""
    creative, _ = _live_creative(client, admin)
    for _ in range(3):
        public.post(f"{BASE}/click", json={"creative_id": creative["id"]})
    assert db.session.get(AdCreative, creative["id"]).clicks == 3


def test_the_rate_limit_on_impression_trips(client, admin, public):
    """One caller in a loop must not be able to inflate a campaign.

    flask_limiter is switched off in the testing config, which is exactly
    why the counters carry their own in-process guard — this asserts the
    guard, because the guard is what is actually deployed everywhere.
    """
    creative, _ = _live_creative(client, admin)
    body = {"creative_id": creative["id"]}

    statuses = [public.post(f"{BASE}/impression", json=body).status_code
                for _ in range(throttle.MAX_EVENTS_PER_WINDOW + 5)]

    assert statuses[0] == 204
    assert 429 in statuses, "the counter endpoint never refused a flood"
    assert statuses[-1] == 429
    assert statuses.index(429) == throttle.MAX_EVENTS_PER_WINDOW
    # Whatever happened to the requests, the number stayed honest.
    assert db.session.get(AdCreative, creative["id"]).impressions == 1


def test_counters_do_not_move_for_a_campaign_that_is_not_live(client, admin, public):
    """Counting against a finished campaign corrupts the report after the fact."""
    creative, campaign = _live_creative(client, admin)
    client.post(f"{BASE}/campaigns/{campaign['id']}/finish", headers=admin)

    result = public.post(f"{BASE}/impression", json={"creative_id": creative["id"]})
    assert result.status_code == 204  # answered identically; nothing leaks
    assert db.session.get(AdCreative, creative["id"]).impressions == 0


def test_counting_survives_a_broken_analytics_collector(client, admin, public, monkeypatch):
    """The event log is a neighbour, not a dependency.

    Whether it is absent, half-deployed or has changed signature, the
    counter on the row still moves and the visitor still gets their 204.
    """
    from app.advertising import routes

    def exploding(*args, **kwargs):
        raise TypeError("record_event() got an unexpected keyword argument")

    creative, _ = _live_creative(client, admin)
    monkeypatch.setattr(routes, "record_event", exploding)
    assert public.post(f"{BASE}/impression",
                       json={"creative_id": creative["id"]}).status_code == 204
    assert db.session.get(AdCreative, creative["id"]).impressions == 1

    monkeypatch.setattr(routes, "record_event", None)
    throttle.reset()
    assert public.post(f"{BASE}/click",
                       json={"creative_id": creative["id"]}).status_code == 204
    assert db.session.get(AdCreative, creative["id"]).clicks == 1


def test_an_unknown_creative_is_answered_like_a_real_one(public):
    """No enumeration oracle on a public endpoint."""
    assert public.post(f"{BASE}/impression", json={"creative_id": 99999999}).status_code == 204


def test_impression_rejects_a_hostile_path(public):
    result = public.post(f"{BASE}/impression",
                         json={"creative_id": 1, "path": "javascript:alert(1)"})
    assert result.status_code == 400


# ── Notices ──────────────────────────────────────────────────────────────────

def _notice(client, admin, **overrides):
    payload = {"code": f"NOTE_{_tag()}", "title": "Dashain office hours"}
    payload.update(overrides)
    result = client.post(f"{BASE}/admin/notices", headers=admin, json=payload)
    assert result.status_code == 201, result.get_json()
    return result.get_json()


def _notices(public, path="/", **kwargs):
    query = "&".join(f"{k}={v}" for k, v in kwargs.items())
    result = public.get(f"{BASE}/notices?path={path}" + (f"&{query}" if query else ""))
    assert result.status_code == 200, result.get_json()
    return result.get_json()["items"]


def test_notices_serve_by_path_in_priority_order(client, admin, public):
    everywhere = _notice(client, admin, title="Everywhere", priority=5, is_active=True)
    pricing = _notice(client, admin, title="Pricing only", priority=50,
                      page_paths=["/pricing"], is_active=True)

    on_pricing = [n["id"] for n in _notices(public, path="/pricing")]
    on_home = [n["id"] for n in _notices(public, path="/")]

    assert on_pricing.index(pricing["id"]) < on_pricing.index(everywhere["id"])
    assert pricing["id"] not in on_home
    assert everywhere["id"] in on_home


def test_an_inactive_notice_is_not_served(client, admin, public):
    notice = _notice(client, admin, is_active=False)
    assert notice["id"] not in [n["id"] for n in _notices(public)]

    client.post(f"{BASE}/admin/notices/{notice['id']}/activate", headers=admin)
    assert notice["id"] in [n["id"] for n in _notices(public)]

    client.post(f"{BASE}/admin/notices/{notice['id']}/deactivate", headers=admin)
    assert notice["id"] not in [n["id"] for n in _notices(public)]


def test_a_notice_outside_its_date_window_is_not_served(client, admin, public):
    expired = _notice(client, admin, is_active=True,
                      starts_at=(datetime.utcnow() - timedelta(days=10)).isoformat(),
                      ends_at=(datetime.utcnow() - timedelta(days=1)).isoformat())
    upcoming = _notice(client, admin, is_active=True,
                       starts_at=(datetime.utcnow() + timedelta(days=1)).isoformat())
    current = _notice(client, admin, is_active=True, starts_at=PAST, ends_at=FUTURE)

    served = [n["id"] for n in _notices(public)]
    assert expired["id"] not in served
    assert upcoming["id"] not in served
    assert current["id"] in served


def test_the_exclude_parameter_drops_already_dismissed_notices(client, admin, public):
    notice = _notice(client, admin, is_active=True)
    assert notice["id"] in [n["id"] for n in _notices(public)]
    assert notice["id"] not in [n["id"] for n in _notices(public, exclude=notice["code"])]


def test_a_notice_that_is_not_dismissible_is_honoured(client, admin, public):
    """A disruption notice may need to be unmissable."""
    notice = _notice(client, admin, is_active=True, is_dismissible=False,
                     tone="disruption")
    assert [n for n in _notices(public) if n["id"] == notice["id"]][0]["is_dismissible"] is False

    refused = public.post(f"{BASE}/notices/{notice['id']}/dismiss")
    assert refused.status_code == 409
    assert db.session.get(SiteNotice, notice["id"]).dismissals == 0


def test_dismissing_counts_once_and_says_where_to_remember_it(client, admin, public):
    """Without preferences consent the dismissal is held for the session only."""
    notice = _notice(client, admin, is_active=True, dismiss_days=30)

    result = public.post(f"{BASE}/notices/{notice['id']}/dismiss")
    assert result.status_code == 200
    body = result.get_json()
    assert body["stored_server_side"] is False
    assert body["store"] == "session"
    assert body["dismiss_days"] == 0
    assert body["code"] == notice["code"]

    # Repeat closes by the same visitor do not inflate the figure.
    public.post(f"{BASE}/notices/{notice['id']}/dismiss")
    assert db.session.get(SiteNotice, notice["id"]).dismissals == 1


def test_dismissing_is_durable_once_preferences_consent_is_given(client, admin, app):
    """With PREFERENCES granted the frontend is told to persist it properly."""
    visitor = app.test_client()
    granted = visitor.post("/api/consent/", json={"categories": ["preferences"]})
    assert granted.status_code == 201, granted.get_json()

    notice = _notice(client, admin, is_active=True, dismiss_days=30)
    body = visitor.post(f"{BASE}/notices/{notice['id']}/dismiss").get_json()
    assert body["store"] == "local"
    assert body["dismiss_days"] == 30


# ── Campaign lifecycle ───────────────────────────────────────────────────────

def test_status_cannot_be_set_by_put(client, admin):
    """Any status by PUT is how a draft ends up live by accident."""
    campaign = _campaign(client, admin)
    result = client.put(f"{BASE}/campaigns/{campaign['id']}", headers=admin,
                        json={"status": "running"})
    assert result.status_code == 400
    assert "activate" in result.get_json()["message"]
    assert db.session.get(AdCampaign, campaign["id"]).status == "draft"


def test_the_lifecycle_actions_enforce_sensible_transitions(client, admin):
    campaign = _campaign(client, admin)
    cid = campaign["id"]

    assert client.post(f"{BASE}/campaigns/{cid}/pause", headers=admin).status_code == 409
    assert _activate(client, admin, cid)["status"] == "running"
    assert client.post(f"{BASE}/campaigns/{cid}/pause",
                       headers=admin).get_json()["status"] == "paused"
    assert _activate(client, admin, cid)["status"] == "running"
    assert client.post(f"{BASE}/campaigns/{cid}/finish",
                       headers=admin).get_json()["status"] == "finished"
    # Finished is terminal: reuse means a new campaign, not a resurrected one.
    assert client.post(f"{BASE}/campaigns/{cid}/activate", headers=admin).status_code == 409
    assert client.post(f"{BASE}/campaigns/{cid}/finish", headers=admin).status_code == 409


def test_a_running_campaign_cannot_be_deleted(client, admin):
    campaign = _live_campaign(client, admin)
    assert client.delete(f"{BASE}/campaigns/{campaign['id']}",
                         headers=admin).status_code == 409


def test_a_placement_with_creatives_cannot_be_deleted(client, admin):
    slot = _placement(client, admin)
    campaign = _campaign(client, admin)
    _make_creative(client, admin, campaign["id"], slot["id"])
    assert client.delete(f"{BASE}/placements/{slot['id']}", headers=admin).status_code == 409


def test_unknown_fields_are_named_rather_than_dropped(client, admin):
    """An editor who misspells a field should not get a 200."""
    result = client.post(f"{BASE}/campaigns", headers=admin, json={
        "code": f"CAMP_{_tag()}", "name": "Typo", "occasionn": "dashain"})
    assert result.status_code == 400
    assert "occasionn" in result.get_json()["message"]


def test_list_endpoints_use_the_items_envelope(client, admin):
    campaign = _campaign(client, admin)
    for url in (f"{BASE}/campaigns", f"{BASE}/placements", f"{BASE}/admin/notices",
                f"{BASE}/campaigns/{campaign['id']}/creatives", f"{BASE}/report"):
        body = client.get(url, headers=admin).get_json()
        assert isinstance(body.get("items"), list), url
        assert isinstance(body.get("total"), int), url


# ── Authorisation ────────────────────────────────────────────────────────────

ADMIN_READS = ["/campaigns", "/placements", "/admin/notices", "/report"]


@pytest.mark.parametrize("path", ADMIN_READS)
def test_admin_endpoints_are_401_for_anonymous(public, path):
    assert public.get(f"{BASE}{path}").status_code == 401


@pytest.mark.parametrize("path", ADMIN_READS)
def test_admin_endpoints_are_403_for_a_plain_customer(client, customer, path):
    assert client.get(f"{BASE}{path}", headers=customer).status_code == 403


def test_a_customer_cannot_create_or_activate_anything(client, admin, customer):
    campaign = _campaign(client, admin)
    assert client.post(f"{BASE}/campaigns", headers=customer,
                       json={"code": f"X_{_tag()}", "name": "Nope"}).status_code == 403
    assert client.post(f"{BASE}/campaigns/{campaign['id']}/activate",
                       headers=customer).status_code == 403
    assert client.delete(f"{BASE}/campaigns/{campaign['id']}",
                         headers=customer).status_code == 403


def test_the_public_endpoints_need_no_account(client, admin, public):
    """Serving is public by design: an advert is not personal data."""
    _placement(client, admin)
    assert public.get(f"{BASE}/serve?path=/").status_code == 200
    assert public.get(f"{BASE}/notices?path=/").status_code == 200


# ── Seed ─────────────────────────────────────────────────────────────────────

def test_the_seed_is_idempotent_and_leaves_everything_inactive(app):
    """Running it on production must never put an advert in front of anyone."""
    from app.advertising.seed import CAMPAIGNS, NOTICES, PLACEMENTS, seed_advertising

    seed_advertising()
    again = seed_advertising()
    assert again == {"placements": 0, "campaigns": 0, "creatives": 0, "notices": 0}

    codes = [code for code, *_ in PLACEMENTS]
    assert AdPlacement.query.filter(AdPlacement.code.in_(codes)).count() == len(PLACEMENTS)

    for spec in CAMPAIGNS:
        campaign = AdCampaign.query.filter_by(code=spec["code"]).one()
        assert campaign.status == AdCampaign.DRAFT
        assert campaign.starts_at is None and campaign.ends_at is None
        assert campaign.is_live() is False
        assert campaign.creatives, f"{spec['code']} seeded no creatives"
        for creative in campaign.creatives:
            assert creative.is_active is False
            # No artwork is seeded, so the alt-text rule can never be dodged.
            assert creative.image_path is None

    for spec in NOTICES:
        notice = SiteNotice.query.filter_by(code=spec["code"]).one()
        assert notice.is_active is False
        assert notice.is_live() is False
        assert notice.starts_at is None and notice.ends_at is None


def test_the_seeded_festival_calendar_covers_the_seasons_the_business_sells_into(app):
    from app.advertising.seed import seed_advertising

    seed_advertising()
    occasions = {c.occasion for c in AdCampaign.query.filter(
        AdCampaign.occasion.isnot(None)).all()}
    assert {"dashain", "tihar", "student_season", "losar"} <= occasions


def test_seeded_paths_are_all_site_relative(app):
    """The seed must not be the thing that introduces an off-site href."""
    from app.advertising.routes import site_path
    from app.advertising.seed import seed_advertising

    seed_advertising()
    for campaign in AdCampaign.query.all():
        if campaign.landing_path:
            assert site_path(campaign.landing_path, "landing_path").startswith("/")
    for creative in AdCreative.query.all():
        if creative.target_path:
            assert site_path(creative.target_path, "target_path").startswith("/")
    for notice in SiteNotice.query.all():
        if notice.link_path:
            assert site_path(notice.link_path, "link_path").startswith("/")


def test_seeded_nepali_is_present_only_where_it_was_written(app):
    """Empty beats wrong: the model falls back to English on its own."""
    from app.advertising.seed import seed_advertising

    seed_advertising()
    student = AdCampaign.query.filter_by(code="STUDENT_EXCESS_BAGGAGE").one()
    for creative in student.creatives:
        # Flagged for a native speaker rather than guessed at.
        assert not creative.headline_ne
        assert creative.to_dict(language="ne")["headline"] == creative.headline

    dashain = AdCampaign.query.filter_by(code="DASHAIN_GIFT_CARGO").one()
    hero = next(c for c in dashain.creatives if c.headline_ne)
    assert hero.to_dict(language="ne")["headline"] == hero.headline_ne


def test_the_seed_command_is_registered(app):
    from app.advertising.commands import register_advertising_commands

    register_advertising_commands(app)
    assert "seed-ads" in app.cli.commands
