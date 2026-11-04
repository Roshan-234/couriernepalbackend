"""The public site must not state things nobody has verified.

This is a twenty-year-old company. A customer who rings a number from the
website and reaches nothing, or walks to a branch that does not exist, is
a worse outcome than a thinner page — and it damages a reputation the
software did nothing to earn.

The starter data was written to make the system work, not to be read by
the public: fourteen branches with plausible addresses and phone numbers
ending `000000`. These tests keep that kind of data off the public API,
and keep the FAQ set from quietly reacquiring claims the business has not
confirmed.
"""

import re

import pytest

from app.content.faqs import FAQS, seed_faqs
from app.extensions import db
from app.models.branch import Branch
from app.models.faq import FAQ
from tests.conftest import auth_header

#: A seeded stand-in rather than a telephone number.
PLACEHOLDER_PHONE = re.compile(r"0{5,}")


@pytest.fixture
def branches(app):
    with app.app_context():
        if not Branch.query.filter_by(code="TRUTH-HQ").first():
            db.session.add(Branch(
                code="TRUTH-HQ", name="Verified Head Office", district="Kathmandu",
                city="Kathmandu", address="Nayabazar, Kathmandu",
                phone="+977-1-5922458", is_active=True, is_published=True))
        if not Branch.query.filter_by(code="TRUTH-FAKE").first():
            db.session.add(Branch(
                code="TRUTH-FAKE", name="Unconfirmed Branch", district="Kaski",
                city="Pokhara", address="Lakeside, Pokhara",
                phone="+977-61-000000", is_active=True))
        db.session.commit()


# ── Branches ─────────────────────────────────────────────────────────────────

def test_the_public_listing_shows_only_confirmed_branches(client, branches):
    r = client.get("/api/branches/")
    assert r.status_code == 200
    codes = {b["code"] for b in r.get_json()["items"]}
    assert "TRUTH-HQ" in codes
    assert "TRUTH-FAKE" not in codes, (
        "an unconfirmed branch is being advertised to the public")


def test_no_published_branch_carries_a_placeholder_phone(client, branches):
    """The specific failure this exists to prevent."""
    r = client.get("/api/branches/")
    offenders = [b["code"] for b in r.get_json()["items"]
                 if PLACEHOLDER_PHONE.search(b.get("phone") or "")]
    assert not offenders, f"placeholder phone numbers on the public site: {offenders}"


def test_an_administrator_still_sees_the_whole_network(client, admin_token, branches):
    """Unpublished branches are real routing destinations, not deleted."""
    r = client.get("/api/branches/", headers=auth_header(admin_token))
    codes = {b["code"] for b in r.get_json()["items"]}
    assert {"TRUTH-HQ", "TRUTH-FAKE"} <= codes


def test_publishing_is_separate_from_being_active(app, branches):
    """Two different questions: may we route through it, and may we print it."""
    with app.app_context():
        unconfirmed = Branch.query.filter_by(code="TRUTH-FAKE").first()
        assert unconfirmed.is_active is True
        assert unconfirmed.is_published is False


def test_a_branch_defaults_to_unpublished(app):
    """A new branch must not reach the website before somebody checks it."""
    with app.app_context():
        default = Branch.__table__.c.is_published.default
        assert default is not None and default.arg is False


def test_an_admin_can_publish_a_branch(client, admin_token, branches, app):
    with app.app_context():
        branch_id = Branch.query.filter_by(code="TRUTH-FAKE").first().id
    r = client.put(f"/api/branches/{branch_id}", json={"is_published": True},
                   headers=auth_header(admin_token))
    assert r.status_code == 200
    assert r.get_json()["is_published"] is True
    # Put it back, so the rest of the suite sees the state it expects.
    client.put(f"/api/branches/{branch_id}", json={"is_published": False},
               headers=auth_header(admin_token))


# ── FAQs ─────────────────────────────────────────────────────────────────────

def test_the_faq_set_is_published_by_a_command_not_only_in_tests(app):
    """The old defaults were inserted only when `current_app.testing`, so
    every real deployment served an empty FAQ page — the same trap the
    service catalogue fell into."""
    with app.app_context():
        result = seed_faqs()
        assert result["total"] == len(FAQS)
        assert FAQ.query.count() >= len(FAQS)


def test_seeding_faqs_twice_creates_nothing_the_second_time(app):
    with app.app_context():
        seed_faqs()
        assert seed_faqs()["created"] == 0


def test_the_faqs_do_not_promise_payment_methods_that_are_not_live(app):
    """Every payment gateway in this system is an inactive seam.

    The previous default set named eSewa, Khalti and card payments. A
    customer reading that on the FAQ page would reasonably expect to pay
    that way.
    """
    text = " ".join(answer for _, answer, _, _ in FAQS).lower()
    for claim in ("esewa", "khalti", "credit/debit", "credit card"):
        assert claim not in text, f"the FAQ promises {claim!r}, which is not live"


def test_the_faqs_use_the_company_contact_details(app):
    """The old set quoted an address the company does not use."""
    text = " ".join(answer for _, answer, _, _ in FAQS)
    assert "moonlightfreight@gmail.com" in text
    assert "info@couriernepal.com" not in text


def test_the_faqs_lead_with_international(app):
    """Domestic is the feeder network, not the business."""
    categories = [category for _, _, category, _ in FAQS]
    assert categories[0] == "international"
    assert categories.count("international") >= 3
