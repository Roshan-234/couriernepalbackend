"""Analytics: the consent rule, the sanitising, and the scheduled jobs.

The load-bearing test in here is
``test_an_event_without_consent_carries_no_visitor_key`` and its twin
``test_an_event_with_consent_carries_a_visitor_key``. Everything else in
this module is a guard rail; those two are the promise the package makes.
They are driven through the real ``/api/consent/`` endpoint rather than by
setting a cookie by hand, so the whole path — decision recorded, cookie
issued, cookie read back on the next request, consent looked up from the
row — is the one under test. Stubbing any of that would leave the most
likely place for this to break untested.
"""

from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.analytics.collect import record_event
from app.models.analytics import AnalyticsDaily, AnalyticsEvent
from app.models.consent import hash_identifier
from tests.conftest import auth_header


BOT_AGENT = "ExampleCrawler/1.0 (+https://example.invalid/crawler)"
HUMAN_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/121.0"


@pytest.fixture
def visitor(app):
    """A browser with its own cookie jar, so consent does not leak between tests."""
    return app.test_client()


def _consent(visitor_client, categories):
    """Record a decision through the real endpoint and keep the cookie."""
    response = visitor_client.post("/api/consent/", json={"categories": categories})
    assert response.status_code == 201, response.get_json()
    return response


def _collect(visitor_client, payload, agent=HUMAN_AGENT, **kwargs):
    return visitor_client.post("/api/analytics/collect", json=payload,
                               headers={"User-Agent": agent}, **kwargs)


def _latest(name=None):
    query = AnalyticsEvent.query
    if name:
        query = query.filter_by(name=name)
    return query.order_by(AnalyticsEvent.id.desc()).first()


def _count():
    return db.session.query(db.func.count(AnalyticsEvent.id)).scalar()


# ── the event vocabulary ─────────────────────────────────────────────────────

def test_a_declared_event_is_accepted(visitor):
    response = _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/services"})
    assert response.status_code == 204
    assert response.data == b""
    assert _latest(AnalyticsEvent.PAGE_VIEW).path == "/services"


def test_an_undeclared_event_is_refused(visitor):
    before = _count()
    response = _collect(visitor, {"name": "exfiltrate_everything", "path": "/"})
    assert response.status_code == 400
    assert _count() == before


def test_a_missing_name_is_refused(visitor):
    assert _collect(visitor, {"path": "/"}).status_code == 400


def test_a_non_object_body_is_refused(visitor):
    assert _collect(visitor, ["page_view"]).status_code == 400


# ── the consent rule ─────────────────────────────────────────────────────────

def test_an_event_without_consent_carries_no_visitor_key(visitor):
    """No decision at all: counted, but anonymous.

    This is the central promise of the module. The hit still exists, so the
    page-view total stays honest; it simply carries nothing that could join
    it to another hit by the same person.
    """
    assert _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW,
                              "path": "/no-consent"}).status_code == 204
    event = _latest()
    assert event.path == "/no-consent"
    assert event.visitor_key is None
    assert event.session_key is None


def test_refusing_analytics_still_carries_no_visitor_key(visitor):
    """A decision that says no is as binding as no decision at all."""
    _consent(visitor, ["preferences"])
    assert _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW,
                              "path": "/refused"}).status_code == 204
    event = _latest()
    assert event.path == "/refused"
    assert event.visitor_key is None
    assert event.session_key is None


def test_an_event_with_consent_carries_a_visitor_key(visitor):
    """Granted analytics consent, through the banner endpoint and its cookie."""
    _consent(visitor, ["analytics"])
    cookie_key = visitor.get_cookie("mf_vid")
    assert cookie_key is not None, "the consent endpoint should have issued the cookie"

    assert _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW,
                              "path": "/consented"}).status_code == 204
    event = _latest()
    assert event.path == "/consented"
    assert event.visitor_key == cookie_key.value
    assert event.session_key is not None
    # The session key is derived, not the subject key wearing a hat.
    assert event.session_key != event.visitor_key


def test_withdrawing_consent_stops_the_visitor_key_immediately(visitor):
    _consent(visitor, ["analytics"])
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/before-withdrawal"})
    assert _latest().visitor_key is not None

    assert visitor.delete("/api/consent/").status_code == 200
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/after-withdrawal"})
    event = _latest()
    assert event.path == "/after-withdrawal"
    assert event.visitor_key is None


def test_a_client_cannot_supply_its_own_visitor_key(visitor):
    """The payload has no say in this. Only the consent record decides."""
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/forged",
                       "visitor_key": "attacker-supplied", "session_key": "nope"})
    event = _latest()
    assert event.visitor_key is None
    assert event.session_key is None


# ── sanitising an untrusted payload ──────────────────────────────────────────

def test_detail_strings_are_truncated_to_the_declared_width(visitor):
    from app.analytics.collect import MAX_DETAIL_VALUE_LENGTH
    _collect(visitor, {"name": AnalyticsEvent.QUOTE_REQUESTED, "path": "/quote",
                       "detail": {"lane": "x" * 500}})
    stored = _latest(AnalyticsEvent.QUOTE_REQUESTED).detail
    assert len(stored["lane"]) == MAX_DETAIL_VALUE_LENGTH


def test_detail_refuses_a_nested_object(visitor):
    before = _count()
    response = _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/",
                                  "detail": {"nested": {"and": "deeper"}}})
    assert response.status_code == 400
    assert _count() == before


def test_detail_refuses_an_array(visitor):
    assert _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/",
                              "detail": {"items": [1, 2, 3]}}).status_code == 400


def test_detail_refuses_too_many_keys(visitor):
    from app.analytics.collect import MAX_DETAIL_KEYS
    payload = {f"k{i}": i for i in range(MAX_DETAIL_KEYS + 3)}
    assert _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/",
                              "detail": payload}).status_code == 400


def test_detail_refuses_a_non_object(visitor):
    assert _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/",
                              "detail": "just a string"}).status_code == 400


def test_a_path_loses_its_query_string(visitor):
    """A query string on an internal URL carries the same risk a referrer does."""
    _collect(visitor, {"name": AnalyticsEvent.TRACKING_LOOKUP,
                       "path": "/track?number=MF123456&email=someone@example.com"})
    event = _latest(AnalyticsEvent.TRACKING_LOOKUP)
    assert event.path == "/track"
    assert "number" not in (event.path or "")
    assert "example.com" not in (event.path or "")


def test_an_over_long_path_is_truncated_not_rejected(visitor):
    width = AnalyticsEvent.__table__.c.path.type.length
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/" + "a" * (width + 50)})
    assert len(_latest().path) == width


# ── the referrer ─────────────────────────────────────────────────────────────

def test_the_referrer_is_reduced_to_a_host(visitor):
    """Never the full referring URL: it carries search terms and private paths."""
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/landing",
                       "referrer": "https://search.example.com/results?q=shipping+to+nepal"})
    event = _latest()
    assert event.referrer_host == "search.example.com"
    assert "shipping" not in (event.referrer_host or "")
    assert "?" not in (event.referrer_host or "")
    assert "/results" not in (event.referrer_host or "")


def test_a_relative_referrer_is_dropped_rather_than_stored(visitor):
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/x",
                       "referrer": "/previous/private/page"})
    assert _latest().referrer_host is None


def test_a_non_http_referrer_scheme_is_dropped(visitor):
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/x",
                       "referrer": "javascript:alert(1)"})
    assert _latest().referrer_host is None


# ── bots and addresses ───────────────────────────────────────────────────────

def test_bot_traffic_is_dropped_rather_than_counted(visitor):
    before = _count()
    response = _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/bot"},
                        agent=BOT_AGENT)
    # The caller is told nothing either way.
    assert response.status_code == 204
    assert _count() == before


def test_the_address_is_hashed_and_never_stored(visitor):
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/ip-check"})
    event = _latest()
    assert event.ip_hash == hash_identifier("127.0.0.1")
    assert len(event.ip_hash) == 64
    # No column on the row holds anything that looks like the address.
    values = [str(value) for value in event.to_dict().values()]
    assert not any("127.0.0.1" in value for value in values)
    assert not hasattr(event, "ip_address")
    assert "127.0.0.1" not in str(event.__dict__)


# ── the no-JS pixel ──────────────────────────────────────────────────────────

def test_the_pixel_counts_a_hit_without_a_cookie_or_a_visitor_key(visitor):
    _consent(visitor, ["analytics"])  # even with consent, the pixel stays anonymous
    before = _count()
    response = visitor.get("/api/analytics/collect.gif?name=page_view&path=/no-js",
                           headers={"User-Agent": HUMAN_AGENT})
    assert response.status_code == 200
    assert response.mimetype == "image/gif"
    assert response.data.startswith(b"GIF89a")
    assert "Set-Cookie" not in response.headers
    assert _count() == before + 1
    event = _latest()
    assert event.path == "/no-js"
    assert event.visitor_key is None
    assert event.session_key is None


def test_the_pixel_still_returns_an_image_for_an_undeclared_name(visitor):
    before = _count()
    response = visitor.get("/api/analytics/collect.gif?name=not_a_real_event")
    assert response.status_code == 200
    assert response.data.startswith(b"GIF89a")
    assert _count() == before


# ── the in-process helper ────────────────────────────────────────────────────

def test_record_event_writes_a_row_outside_http(app):
    before = _count()
    event = record_event(AnalyticsEvent.AD_IMPRESSION, path="/server-side",
                         detail={"placement": "sidebar"})
    assert event is not None
    assert _count() == before + 1
    assert event.visitor_key is None  # no request, so no consent, so no key


def test_record_event_never_raises(app):
    """Its whole contract: a failed analytics write must not break its caller."""
    assert record_event("not_a_declared_event") is None
    assert record_event(AnalyticsEvent.PAGE_VIEW, detail={"a": {"nested": 1}}) is not None
    assert record_event(AnalyticsEvent.PAGE_VIEW, nonsense_field="ignored") is not None
    assert record_event(None) is None


def test_record_event_ignores_a_caller_supplied_visitor_key(app):
    event = record_event(AnalyticsEvent.PAGE_VIEW, visitor_key="smuggled")
    assert event is not None
    assert event.visitor_key is None


# ── admin reporting ──────────────────────────────────────────────────────────

ADMIN_ENDPOINTS = ("/api/analytics/overview", "/api/analytics/events",
                   "/api/analytics/daily", "/api/analytics/realtime")


@pytest.mark.parametrize("endpoint", ADMIN_ENDPOINTS)
def test_admin_endpoints_refuse_an_anonymous_caller(visitor, endpoint):
    # A fresh client, not the shared one: tokens are accepted from cookies as
    # well as headers, so the session-scoped client is signed in as whoever
    # logged in last and would get 403 rather than 401.
    assert visitor.get(endpoint).status_code == 401


@pytest.mark.parametrize("endpoint", ADMIN_ENDPOINTS)
def test_admin_endpoints_answer_an_administrator(client, admin_token, endpoint):
    response = client.get(endpoint, headers=auth_header(admin_token))
    assert response.status_code == 200


def test_events_listing_never_returns_the_pseudonymous_identifiers(visitor, client, admin_token):
    _consent(visitor, ["analytics"])
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/identifier-check"})
    key = visitor.get_cookie("mf_vid").value
    assert _latest().visitor_key == key  # it is stored...

    response = client.get("/api/analytics/events?per_page=100",
                          headers=auth_header(admin_token))
    assert response.status_code == 200
    body = response.get_json()
    assert body["items"] and "total" in body
    for item in body["items"]:
        assert "visitor_key" not in item
        assert "session_key" not in item
        assert "ip_hash" not in item
    # ...and it is nowhere in what an administrator is shown.
    assert key not in response.get_data(as_text=True)
    assert hash_identifier("127.0.0.1") not in response.get_data(as_text=True)


def test_events_listing_filters(visitor, client, admin_token):
    _collect(visitor, {"name": AnalyticsEvent.CONTACT_SUBMITTED, "path": "/filter-me",
                       "utm_campaign": "diwali2026"})
    response = client.get("/api/analytics/events?name=contact_submitted&path=/filter-me",
                          headers=auth_header(admin_token))
    body = response.get_json()
    assert body["total"] >= 1
    assert all(item["path"] == "/filter-me" for item in body["items"])
    assert all(item["name"] == "contact_submitted" for item in body["items"])

    campaign = client.get("/api/analytics/events?utm_campaign=diwali2026",
                          headers=auth_header(admin_token)).get_json()
    assert campaign["total"] >= 1


def test_events_listing_refuses_an_undeclared_name_filter(client, admin_token):
    response = client.get("/api/analytics/events?name=whatever",
                          headers=auth_header(admin_token))
    assert response.status_code == 400


def test_overview_is_honest_about_the_unique_visitor_count(visitor, client, admin_token):
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/anon-overview"})
    body = client.get("/api/analytics/overview",
                      headers=auth_header(admin_token)).get_json()

    assert body["unique_visitors_is_floor"] is True
    assert body["unique_visitors_source"] in ("events", "daily_rollup")
    assert "consented" in body["unique_visitors_basis"].lower()
    # The size of what the figure cannot see is reported next to it.
    assert body["events_without_visitor_key"] >= 1
    assert body["unique_visitors"] <= body["total_events"]


def test_overview_shape_and_totals(visitor, client, admin_token):
    _collect(visitor, {"name": AnalyticsEvent.SHIPMENT_BOOKED, "path": "/book",
                       "referrer": "https://partner.example.org/promo"})
    body = client.get("/api/analytics/overview",
                      headers=auth_header(admin_token)).get_json()

    for key in ("total_events", "page_views", "series", "top_paths",
                "top_referrer_hosts", "top_countries", "devices", "conversions"):
        assert key in body, key
    assert body["days"] == len(body["series"]) == 30
    assert body["series"][-1]["day"] == datetime.utcnow().date().isoformat()
    for name in ("quote_requested", "tracking_lookup", "shipment_booked",
                 "contact_submitted"):
        assert name in body["conversions"]
    assert body["conversions"]["shipment_booked"] >= 1
    assert "partner.example.org" in [row["value"] for row in body["top_referrer_hosts"]]
    assert body["total_events"] == sum(body["events_by_name"].values())


def test_overview_rejects_an_absurd_range(client, admin_token):
    response = client.get("/api/analytics/overview?from=1999-01-01",
                          headers=auth_header(admin_token))
    assert response.status_code == 400
    assert client.get("/api/analytics/overview?from=2026-02-01&to=2026-01-01",
                      headers=auth_header(admin_token)).status_code == 400
    assert client.get("/api/analytics/overview?from=not-a-date",
                      headers=auth_header(admin_token)).status_code == 400


def test_realtime_shows_what_just_happened(visitor, client, admin_token):
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/just-now"})
    body = client.get("/api/analytics/realtime", headers=auth_header(admin_token)).get_json()
    assert body["minutes"] == 30
    assert body["total_events"] >= 1
    assert body["items"][0]["path"] == "/just-now"
    assert all("value" in row and "events" in row for row in body["active_paths"])
    assert all("visitor_key" not in item and "ip_hash" not in item
               for item in body["items"])


# ── the rollup ───────────────────────────────────────────────────────────────

def test_the_rollup_is_idempotent_across_two_runs(app, visitor):
    today = datetime.utcnow().date()
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/rollup-me"})
    _collect(visitor, {"name": AnalyticsEvent.PAGE_VIEW, "path": "/rollup-me"})

    runner = app.test_cli_runner()
    first = runner.invoke(args=["analytics-rollup", "--day", today.isoformat()])
    assert first.exit_code == 0, first.output
    db.session.rollback()
    after_first = {(row.name, row.path): row.events
                   for row in AnalyticsDaily.query.filter_by(day=today).all()}

    second = runner.invoke(args=["analytics-rollup", "--day", today.isoformat()])
    assert second.exit_code == 0, second.output
    db.session.rollback()
    after_second = {(row.name, row.path): row.events
                    for row in AnalyticsDaily.query.filter_by(day=today).all()}

    assert after_first == after_second, "a second run duplicated or changed the rollup"
    assert after_first[(AnalyticsEvent.PAGE_VIEW, "/rollup-me")] == 2


def test_the_rollup_totals_match_a_direct_count(app, visitor):
    today = datetime.utcnow().date()
    _collect(visitor, {"name": AnalyticsEvent.QUOTE_REQUESTED, "path": "/totals"})

    runner = app.test_cli_runner()
    assert runner.invoke(args=["analytics-rollup", "--day", today.isoformat()]).exit_code == 0
    db.session.rollback()

    rolled = db.session.query(db.func.coalesce(db.func.sum(AnalyticsDaily.events), 0)).filter(
        AnalyticsDaily.day == today).scalar()
    counted = db.session.query(db.func.count(AnalyticsEvent.id)).filter(
        AnalyticsEvent.occurred_at >= datetime.combine(today, datetime.min.time()),
    ).scalar()
    assert rolled == counted


def test_the_rollup_coerces_a_null_path_so_the_unique_constraint_holds(app):
    """A NULL path would re-insert every run: in SQL, NULL != NULL."""
    from app.analytics.commands import rollup_day
    today = datetime.utcnow().date()
    db.session.add(AnalyticsEvent(name=AnalyticsEvent.NOTICE_SHOWN, path=None,
                                  occurred_at=datetime.utcnow()))
    db.session.commit()

    rollup_day(today)
    rollup_day(today)
    rows = AnalyticsDaily.query.filter_by(day=today, name=AnalyticsEvent.NOTICE_SHOWN).all()
    assert len(rows) == 1
    assert rows[0].path == ""


def test_the_rollup_can_walk_a_backlog(app):
    from app.analytics.commands import rollup_range
    today = datetime.utcnow().date()
    results = rollup_range(today - timedelta(days=3), today)
    assert [row["day"] for row in results] == [
        (today - timedelta(days=offset)).isoformat() for offset in (3, 2, 1, 0)]


def test_the_rollup_refuses_an_unreasonable_backlog(app):
    import click
    from app.analytics.commands import MAX_BACKLOG_DAYS, rollup_range
    today = datetime.utcnow().date()
    with pytest.raises(click.ClickException):
        rollup_range(today - timedelta(days=MAX_BACKLOG_DAYS + 5), today)


# ── retention ────────────────────────────────────────────────────────────────

@pytest.fixture
def purgeable(app):
    """One auto-purge rule and one event old enough to fall under it."""
    from app.models.privacy import RetentionRule
    rule = RetentionRule(code="test_analytics_purge", label="Test rule",
                         target="AnalyticsEvent", retain_days=30,
                         auto_purge=True, is_active=True)
    stale = AnalyticsEvent(name=AnalyticsEvent.PAGE_VIEW, path="/ancient",
                           occurred_at=datetime.utcnow() - timedelta(days=400))
    db.session.add_all([rule, stale])
    db.session.commit()
    # Held as plain ids: a test here may legitimately delete the event, and
    # reading .id off a deleted instance in teardown triggers a refresh.
    rule_id, stale_id = rule.id, stale.id
    yield rule, stale
    db.session.rollback()
    for model, row_id in ((RetentionRule, rule_id), (AnalyticsEvent, stale_id)):
        row = db.session.get(model, row_id)
        if row is not None:
            db.session.delete(row)
    db.session.commit()


def test_retention_purge_dry_run_deletes_nothing(app, purgeable):
    rule, stale = purgeable
    before = _count()

    runner = app.test_cli_runner()
    result = runner.invoke(args=["retention-purge", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would delete" in result.output
    assert "Dry run" in result.output

    db.session.rollback()
    assert _count() == before
    assert db.session.get(AnalyticsEvent, stale.id) is not None
    # A dry run leaves no trace on the rule either, so it can be repeated.
    assert db.session.get(type(rule), rule.id).last_purged_at is None


def test_retention_purge_defaults_to_a_dry_run_with_no_flag_at_all(app, purgeable):
    before = _count()
    result = app.test_cli_runner().invoke(args=["retention-purge"])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    db.session.rollback()
    assert _count() == before


def test_retention_purge_reports_the_row_it_would_take(app, purgeable):
    from app.analytics.commands import purge_expired
    report = {entry["code"]: entry for entry in purge_expired(apply=False)}
    assert report["test_analytics_purge"]["would_delete"] >= 1
    assert report["test_analytics_purge"]["applied"] is False


def test_retention_purge_apply_deletes_and_records_what_it_took(app, purgeable):
    rule, stale = purgeable
    stale_id = stale.id

    result = app.test_cli_runner().invoke(args=["retention-purge", "--apply"])
    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output

    db.session.rollback()
    assert db.session.get(AnalyticsEvent, stale_id) is None
    refreshed = db.session.get(type(rule), rule.id)
    assert refreshed.last_purged_at is not None
    assert refreshed.last_purged_count >= 1


def test_retention_purge_skips_a_target_it_has_no_handler_for(app):
    """An instruction to delete from a table nobody mapped is reported, not guessed."""
    from app.analytics.commands import purge_expired
    from app.models.privacy import RetentionRule
    rule = RetentionRule(code="test_unknown_target", label="Unknown",
                         target="SomethingNobodyWrote", retain_days=10,
                         auto_purge=True, is_active=True)
    db.session.add(rule)
    db.session.commit()
    try:
        report = {entry["code"]: entry for entry in purge_expired(apply=False)}
        assert "no purge handler" in report["test_unknown_target"]["skipped"]
        assert report["test_unknown_target"]["deleted"] == 0
    finally:
        db.session.rollback()
        db.session.delete(db.session.get(RetentionRule, rule.id))
        db.session.commit()


def test_retention_purge_ignores_a_rule_with_auto_purge_off(app):
    from app.analytics.commands import purge_expired
    from app.models.privacy import RetentionRule
    rule = RetentionRule(code="test_manual_only", label="Manual", target="AnalyticsEvent",
                         retain_days=1, auto_purge=False, is_active=True)
    db.session.add(rule)
    db.session.commit()
    try:
        assert "test_manual_only" not in {e["code"] for e in purge_expired(apply=False)}
    finally:
        db.session.rollback()
        db.session.delete(db.session.get(RetentionRule, rule.id))
        db.session.commit()


# ── no outbound anything ─────────────────────────────────────────────────────

def test_the_package_makes_no_outbound_calls():
    """The point of measuring here is that nothing leaves. Keep it that way."""
    import pathlib
    package = pathlib.Path(__file__).resolve().parent.parent / "app" / "analytics"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    for forbidden in ("import requests", "urllib.request", "httpx", "urlopen",
                      "socket.create_connection"):
        assert forbidden not in source, f"{forbidden} has no business in this package"
