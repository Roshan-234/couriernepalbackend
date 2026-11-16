"""What the API lets a cache keep, and what it must not.

The public site is static HTML now, but every page view still called Flask
five or six times for things that change about once a week — the service
catalogue, the FAQ, the advert slots, the cookie policy. On shared hosting
that is a Python process woken for every visitor to serve a constant, and
it was the next bottleneck after prerendering.

Caching is the fix and also the easiest way to serve one customer's data to
another, so the rule is an allowlist and the tests below are mostly about
what stays uncacheable. The ordering that matters:

* a path must be named to be cacheable at all;
* an authenticated request is never cached, even on a named path, because
  the handler may have answered differently for that person;
* a request carrying any cookie gets `private` at most, never `public`.
"""

import pytest

from app.security import (
    PUBLIC_CACHE_SECONDS,
    _AUTH_COOKIES,
    _CACHEABLE_EXACT,
    _CACHEABLE_PREFIXES,
)
from tests.conftest import auth_header


def _cache(response):
    return response.headers.get("Cache-Control", "")


@pytest.fixture
def anonymous(app):
    return app.test_client()


# ── What may be cached ───────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/consent/policy",
    "/api/faq/",
    "/api/services/",
    "/api/reviews/summary",
    "/api/ads/serve",
    "/api/ads/notices",
])
def test_a_public_constant_is_cacheable(anonymous, path):
    response = anonymous.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} answered {response.status_code} in this database")
    assert "no-store" not in _cache(response)
    assert f"max-age={PUBLIC_CACHE_SECONDS}" in _cache(response)


def test_an_anonymous_caller_gets_a_shared_cache(anonymous):
    response = anonymous.get("/api/consent/policy")
    assert _cache(response).startswith("public,")


def test_a_cacheable_response_declares_what_it_varies_on(anonymous):
    """Adverts differ by country, so a shared cache must not reuse one
    visitor's copy for somebody somewhere else."""
    response = anonymous.get("/api/ads/serve", query_string={"path": "/"})
    assert "CF-IPCountry" in response.headers.get("Vary", "")


# ── What must not be ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/consent/",            # this visitor's own decision
    "/api/shipments/page",      # somebody's parcels
    "/api/reviews/admin",       # the moderation queue
    "/api/notifications/logs",  # who was written to
])
def test_personal_or_privileged_paths_are_never_cached(anonymous, path):
    assert _cache(anonymous.get(path)) == "no-store"


def test_an_authenticated_request_is_never_cached(client, admin_token):
    """The same path can answer differently for an administrator.

    `/api/branches/` returns the whole network to an admin and only the
    published branches to everybody else. Caching that would put the
    unpublished network in front of the public.
    """
    response = client.get("/api/branches/", headers=auth_header(admin_token))
    assert response.status_code == 200
    assert _cache(response) == "no-store"


def test_an_auth_cookie_disqualifies_a_response(app):
    client = app.test_client()
    client.set_cookie("access_token", "whatever", domain="localhost")
    assert _cache(client.get("/api/faq/")) == "no-store"


def test_an_ordinary_cookie_downgrades_to_private(app):
    """A returning visitor carries the consent cookie.

    Their browser may keep a copy -- that is what removes the repeat calls
    as they move between pages -- but a shared cache must not, because the
    cookie is an identifier.
    """
    client = app.test_client()
    client.set_cookie("mf_vid", "abc123", domain="localhost")
    header = _cache(client.get("/api/faq/"))
    assert header.startswith("private,")
    assert "public" not in header


def test_a_write_is_never_cached(anonymous):
    response = anonymous.post("/api/consent/", json={"categories": []})
    assert _cache(response) == "no-store"


def test_an_error_is_never_cached(anonymous):
    """A 404 cached for three minutes is a page that stays broken."""
    response = anonymous.get("/api/content/page", query_string={"path": "/nope"})
    if response.status_code == 200:
        pytest.skip("that path exists in this database")
    assert _cache(response) == "no-store"


# ── The allowlist itself ─────────────────────────────────────────────────────

def test_nothing_personal_is_on_the_allowlist():
    """A guard on the guard.

    Adding a path here asserts that two different visitors may be served
    the same bytes. These are the ones where that is false.
    """
    # Prefixes where nothing below them may ever be cached.
    forbidden_prefixes = ("/api/tracking", "/api/shipments", "/api/invoices",
                          "/api/packing-lists", "/api/users", "/api/claims",
                          "/api/cod", "/api/payments", "/api/audit",
                          "/api/sessions", "/api/notifications",
                          "/api/privacy", "/api/marketing", "/api/analytics")
    # The consent *state* is personal; the consent *policy* is the published
    # wording every visitor sees, so it is named exactly rather than by
    # prefix -- a prefix rule would have banned the one that is fine.
    forbidden_exact = ("/api/consent/", "/api/consent")

    for path in tuple(_CACHEABLE_EXACT) + _CACHEABLE_PREFIXES:
        assert path not in forbidden_exact, f"{path} must not be cacheable"
        for bad in forbidden_prefixes:
            assert not path.startswith(bad), f"{path} must not be cacheable"


def test_no_admin_path_is_cacheable():
    for path in tuple(_CACHEABLE_EXACT) + _CACHEABLE_PREFIXES:
        assert "/admin" not in path


def test_the_auth_cookie_list_matches_what_login_sets(client):
    """If login starts setting a differently named cookie, this list has to
    learn about it or authenticated responses become cacheable."""
    from tests.conftest import _register

    email = "cache_cookie_probe@test.com"
    _register(client, email)
    response = client.post("/api/auth/login",
                           json={"email": email, "password": "Admin1234!"})
    assert response.status_code == 200

    # Read the names straight off Set-Cookie rather than the client's jar,
    # whose shape differs between Werkzeug versions.
    issued = {header.split("=", 1)[0].strip()
              for key, header in response.headers
              if key.lower() == "set-cookie"}
    assert issued, "login set no cookies at all"
    assert any(name in _AUTH_COOKIES for name in issued), (
        f"login set {sorted(issued)}, none of which is treated as an auth "
        f"cookie, so an authenticated response would be cacheable")
