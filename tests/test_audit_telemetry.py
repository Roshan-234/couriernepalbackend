"""Public telemetry must not land in the administrative audit log.

The audit log exists so that somebody can later ask who changed what. Every
`/api/*` request used to add a row to it, which was fine while the API only
served deliberate actions. It stopped being fine when the site gained an
analytics beacon and advert slots: those fire on every page view, so the log
would grow with *visitors* rather than with actions, and the sign-ins and
record changes it exists to capture would be buried under beacon traffic.

The second problem is the one that mattered more. `audit_request` stores
`client_ip()` — the visitor's real address — while the analytics row written
by the very same request deliberately stores only a salted hash. Logging both
would hand back the identifier the analytics design spent effort not keeping,
and would contradict what the published privacy page tells people.

So these tests pin three things: beacons are not audited, real decisions on
the same namespaces still are, and no raw address reaches `audit_logs` from
a page view.
"""

import pytest

from app.extensions import db
from app.models.audit_log import AuditLog


@pytest.fixture
def visitor(app):
    return app.test_client()


def _audit_count():
    return AuditLog.query.count()


def _latest_actions(limit=6):
    rows = (AuditLog.query.order_by(AuditLog.id.desc()).limit(limit).all())
    return [(r.action, r.ip_address) for r in rows]


# ── Beacons are not audited ──────────────────────────────────────────────────

@pytest.mark.parametrize("method, path, payload", [
    ("post", "/api/analytics/collect", {"name": "page_view", "path": "/"}),
    ("get", "/api/analytics/collect.gif", None),
    ("get", "/api/ads/serve", None),
    ("get", "/api/ads/notices", None),
    ("post", "/api/ads/impression", {"creative_id": 1}),
    ("post", "/api/ads/click", {"creative_id": 1}),
])
def test_telemetry_writes_no_audit_row(app, visitor, method, path, payload):
    """Whatever these return, they must not grow the audit log.

    The status is deliberately not asserted: an endpoint may 404 while its
    module is still being wired, and the point of the test holds either way.
    """
    with app.app_context():
        before = _audit_count()

    call = getattr(visitor, method)
    call(path, json=payload) if payload is not None else call(path)

    with app.app_context():
        assert _audit_count() == before, (
            f"{method.upper()} {path} wrote an audit row; it fires on every "
            f"page view. Recent: {_latest_actions()}")


def test_a_page_view_beacon_stores_no_address_anywhere(app, visitor):
    """The analytics row keeps a hash. Nothing alongside it may keep the address."""
    address = "198.51.100.77"
    with app.app_context():
        before = _audit_count()

    visitor.post("/api/analytics/collect",
                 json={"name": "page_view", "path": "/pricing"},
                 environ_base={"REMOTE_ADDR": address})

    with app.app_context():
        assert _audit_count() == before
        assert not AuditLog.query.filter_by(ip_address=address).first(), (
            "the visitor's address reached audit_logs from a page view")


def test_consent_state_polling_is_not_audited(app, visitor):
    """Every page asks the server what the visitor agreed to."""
    with app.app_context():
        before = _audit_count()
    visitor.get("/api/consent/")
    visitor.get("/api/consent/policy")
    with app.app_context():
        assert _audit_count() == before


# ── Real decisions on the same namespaces still are ──────────────────────────

def test_recording_a_consent_decision_is_audited(app, visitor):
    """A person deciding something is exactly what the log is for."""
    with app.app_context():
        before = _audit_count()
    r = visitor.post("/api/consent/", json={"categories": ["analytics"]})
    assert r.status_code == 201
    with app.app_context():
        assert _audit_count() == before + 1, (
            "a consent decision must stay auditable; only the polling is dropped")


def test_withdrawing_consent_is_audited(app, visitor):
    visitor.post("/api/consent/", json={"categories": ["analytics"]})
    with app.app_context():
        before = _audit_count()
    visitor.delete("/api/consent/")
    with app.app_context():
        assert _audit_count() == before + 1


def test_admin_reading_the_consent_log_is_audited(app, client, admin_token):
    """Skipping the polled reads must not skip the namespace's admin reads.

    These are matched exactly rather than by prefix precisely so that
    somebody opening the consent register leaves a trace.
    """
    with app.app_context():
        before = _audit_count()
    r = client.get("/api/consent/records",
                   headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    with app.app_context():
        assert _audit_count() == before + 1


def test_the_skip_list_only_holds_public_telemetry(app):
    """A guard on the guard.

    It is easy to quieten a noisy endpoint by adding it here, and that is how
    an audit log stops recording the thing somebody later needs. Everything
    listed has to be public, high-volume and either read-only or a counter.
    """
    from app.middleware.audit import HIGH_VOLUME_PATHS, POLLED_READ_PATHS

    allowed = {"/api/analytics/collect", "/api/ads/serve", "/api/ads/notices",
               "/api/ads/impression", "/api/ads/click"}
    assert set(HIGH_VOLUME_PATHS) == allowed, (
        "the telemetry skip list changed; confirm the addition is public, "
        "high-volume and not a record change")
    assert POLLED_READ_PATHS == {"/api/consent", "/api/consent/",
                                 "/api/consent/policy"}
    # Prefix matching must not reach the administrative endpoints.
    assert not any("/api/consent/records".startswith(p) for p in HIGH_VOLUME_PATHS)
