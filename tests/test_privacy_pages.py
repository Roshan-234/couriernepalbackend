"""The published privacy and cookie pages must describe the real system.

A privacy policy is a representation to the public. If it says the site loads
Google Analytics and the site does not, the page is wrong in the direction
that matters least; if it says nothing is stored before consent and something
is, the page is wrong in the direction that gets a company fined. These tests
pin the second kind: the claims the pages make are checked against the code
that makes them true, so a future change cannot quietly falsify the policy.
"""

import pytest

from app.content.privacy_pages import PAGES, PRIVACY, seed_privacy_pages
from app.models.consent import CONSENT_LIFETIME_DAYS
from app.models.privacy import DataRequest
from app.models.site_page import SitePage


@pytest.fixture(scope="module", autouse=True)
def pages(app):
    with app.app_context():
        seed_privacy_pages()


def _page(path):
    return SitePage.query.filter_by(path=path).first()


def _all_text(path):
    page = _page(path)
    return " ".join([page.intro] + [s["body"] for s in page.sections]).lower()


def test_all_three_pages_are_published(app):
    with app.app_context():
        for page in PAGES:
            row = _page(page["path"])
            assert row is not None, f"{page['path']} was not seeded"
            assert row.published is True
            assert len(row.sections) >= 5


def test_the_pages_are_reachable_through_the_content_api(client):
    for path in ("/privacy", "/cookies", "/data-requests"):
        r = client.get("/api/content/page", query_string={"path": path})
        assert r.status_code == 200, f"{path} not served: {r.status_code}"
        assert r.get_json()["path"] == path


# ── Claims checked against the code ──────────────────────────────────────────

def test_the_cookie_page_no_longer_claims_google_analytics(app):
    """The old page said GA was loaded after consent. It is not loaded at all."""
    with app.app_context():
        privacy = _all_text("/privacy")
        assert "google analytics" not in privacy
        # The cookies page may mention it only to say it has been removed.
        cookies = _all_text("/cookies")
        if "google analytics" in cookies:
            assert "no longer" in cookies or "removed" in cookies


def test_no_third_party_analytics_script_is_served_anywhere(app):
    """The claim 'measured on our own servers' has to be true of the backend."""
    import pathlib
    import re

    # Anchored so that `BlogTag(` does not read as `gtag(`, which is exactly
    # what a looser version of this test matched on first writing.
    suspect = {
        "googletagmanager.com": r"googletagmanager\.com",
        "google-analytics.com": r"google-analytics\.com",
        "gtag()": r"\bgtag\s*\(",
        "dataLayer": r"\bdataLayer\b",
        "facebook pixel": r"connect\.facebook\.net",
        "hotjar": r"\bhotjar\b",
        "clarity": r"clarity\.ms",
    }
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for file in root.rglob("*.py"):
        text = file.read_text(encoding="utf-8", errors="ignore")
        offenders += [f"{file.name}: {label}"
                      for label, pattern in suspect.items()
                      if re.search(pattern, text, re.IGNORECASE)]
    assert not offenders, f"Third-party analytics referenced in: {offenders}"


def test_the_stated_response_deadline_matches_the_code(app):
    with app.app_context():
        text = _all_text("/privacy") + _all_text("/data-requests")
        assert str(DataRequest.RESPONSE_DAYS) in text


def test_the_stated_consent_lifetime_matches_the_code(app):
    with app.app_context():
        assert str(CONSENT_LIFETIME_DAYS) in _all_text("/cookies")


def test_the_cookie_page_names_the_cookie_it_sets(app):
    """A cookie table that omits the cookie actually set is not a cookie table."""
    from app.visitor import VISITOR_COOKIE
    with app.app_context():
        assert VISITOR_COOKIE in _all_text("/cookies")


def test_the_pages_promise_nothing_before_a_choice_is_made(app):
    """Cross-check the page's central promise against the consent endpoint."""
    with app.app_context():
        assert "until you answer" in _all_text("/cookies")
    fresh = app.test_client()
    r = fresh.get("/api/consent/")
    assert "mf_vid" not in r.headers.get("Set-Cookie", "")


def test_withdrawal_is_promised_and_delivered(app):
    with app.app_context():
        assert "withdraw" in _all_text("/cookies")
    client = app.test_client()
    client.post("/api/consent/", json={"categories": ["analytics"]})
    assert client.delete("/api/consent/").status_code == 200


# ── Honesty about what is not settled ────────────────────────────────────────

def test_unanswered_legal_questions_are_marked_not_guessed(app):
    """Facts about the company are flagged rather than invented.

    If a future edit removes these markers, it should be because somebody
    answered the question — not because the placeholder looked untidy.
    """
    with app.app_context():
        privacy = " ".join(s["body"] for s in _page("/privacy").sections)
        assert "[TO CONFIRM" in privacy
        # The transfer question is the one with a real obligation behind it.
        assert "adequacy" in privacy.lower()


def test_seeding_is_idempotent(app):
    with app.app_context():
        assert all(outcome == "already current"
                   for outcome in seed_privacy_pages().values())


def test_these_pages_are_owned_by_code_not_the_page_editor(app):
    """An edit through the page editor is overwritten, on purpose.

    Everywhere else on this site the administrator's wording wins. Not here:
    the tests above check the published text against the constants that make
    it true, and a policy that can drift from the system it describes is the
    failure those checks exist to catch. The seeder therefore refreshes these
    three on every boot — and logs a warning naming the path, so the overwrite
    is visible rather than silent.
    """
    from app.extensions import db

    with app.app_context():
        page = _page("/cookies")
        page.intro = "Hand-written by the office."
        db.session.commit()

        assert seed_privacy_pages()["/cookies"] == "refreshed from code"
        assert _page("/cookies").intro != "Hand-written by the office."
        assert str(CONSENT_LIFETIME_DAYS) in _all_text("/cookies")


def test_overwriting_an_edit_is_logged(app, caplog):
    """The warning is the only thing standing between an overwrite and a
    confused administrator, so it is worth a test of its own."""
    import logging

    from app.extensions import db

    with app.app_context():
        page = _page("/data-requests")
        page.intro = "Something somebody typed."
        db.session.commit()

        with caplog.at_level(logging.WARNING):
            seed_privacy_pages()

        assert any("/data-requests" in record.message
                   and "privacy_pages.py" in record.message
                   for record in caplog.records), (
            "overwriting an edit must say so, and say where to edit instead")


def test_a_second_revision_of_our_own_text_still_lands(app):
    """The regression that prompted the ownership change.

    The first version of this seeder replaced `/privacy` only while it held
    the original pre-consent default. That works exactly once: after this
    module has published, its next revision is indistinguishable from an
    administrator's edit and gets skipped, freezing the policy at whatever
    was true the first time. Simulated here by shipping a changed section.
    """
    from app.extensions import db

    with app.app_context():
        page = _page("/privacy")
        # Stand in for "the policy text moved on in a later release".
        page.sections = page.sections[:-1]
        db.session.commit()

        assert seed_privacy_pages()["/privacy"] == "refreshed from code"
        assert len(_page("/privacy").sections) == len(PRIVACY["sections"])
