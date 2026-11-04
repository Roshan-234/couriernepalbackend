"""The admin API must return what an administrator is editing.

`AdCreative.to_dict` and `SiteNotice.to_dict` were written for the public
page: they resolve the headline against one language and drop the Nepali
columns, the page paths and the country list. Every admin endpoint used
that same shape, which had two consequences, the second serious:

* the management screens could not show which creatives are still waiting
  on a native Nepali speaker, even though that is tracked work; and
* an editor that loads a form from this shape renders those boxes empty,
  so saving an untouched form would blank whatever Nepali copy was stored.

So the admin endpoints now pass `admin=True`. These tests pin both halves:
the admin shape carries the stored values, and the public shape still does
not — the second matters because targeting rules and unused translations
have no business being sent to a visitor's browser.
"""

import pytest

from app.extensions import db
from app.models.advertising import AdCampaign, AdCreative, AdPlacement, SiteNotice

NEPALI_HEADLINE = "दशैंमा उपहार"
NEPALI_BODY = "काठमाडौंबाट"


@pytest.fixture
def fixtures(app):
    with app.app_context():
        placement = AdPlacement.query.filter_by(code="admin_fields_slot").first()
        if not placement:
            placement = AdPlacement(code="admin_fields_slot", name="Admin field slot")
            db.session.add(placement)
            db.session.flush()
        campaign = AdCampaign.query.filter_by(code="ADMIN_FIELDS").first()
        if not campaign:
            campaign = AdCampaign(code="ADMIN_FIELDS", name="Admin field campaign")
            db.session.add(campaign)
            db.session.flush()
        creative = AdCreative.query.filter_by(headline="Admin field creative").first()
        if not creative:
            creative = AdCreative(
                campaign_id=campaign.id, placement_id=placement.id,
                headline="Admin field creative", body="English body",
                headline_ne=NEPALI_HEADLINE, body_ne=NEPALI_BODY,
                page_paths=["/pricing"], countries=["AE", "QA"],
            )
            db.session.add(creative)
        notice = SiteNotice.query.filter_by(code="ADMIN_FIELDS_NOTICE").first()
        if not notice:
            notice = SiteNotice(
                code="ADMIN_FIELDS_NOTICE", title="Admin field notice",
                body="English body", title_ne=NEPALI_HEADLINE,
                page_paths=["/pricing"],
            )
            db.session.add(notice)
        db.session.commit()
        return {"creative_id": creative.id, "notice_id": notice.id,
                "campaign_id": campaign.id}


# ── The admin shape carries what is being edited ─────────────────────────────

def test_admin_creative_shape_includes_the_stored_nepali(app, fixtures):
    with app.app_context():
        creative = db.session.get(AdCreative, fixtures["creative_id"])
        payload = creative.to_dict(admin=True)
        assert payload["headline_ne"] == NEPALI_HEADLINE
        assert payload["body_ne"] == NEPALI_BODY
        # The English stays English even in admin mode: an editor is not
        # looking at a resolved view, it is looking at two separate fields.
        assert payload["headline"] == "Admin field creative"


def test_admin_creative_shape_includes_targeting(app, fixtures):
    with app.app_context():
        payload = db.session.get(AdCreative, fixtures["creative_id"]).to_dict(admin=True)
        assert payload["page_paths"] == ["/pricing"]
        assert payload["countries"] == ["AE", "QA"]


def test_missing_nepali_is_flagged_rather_than_inferred(app, fixtures):
    """The management screens need this to be a field, not a string compare."""
    with app.app_context():
        complete = db.session.get(AdCreative, fixtures["creative_id"])
        assert complete.to_dict(admin=True)["needs_nepali"] is False

        bare = AdCreative(campaign_id=fixtures["campaign_id"],
                          placement_id=complete.placement_id,
                          headline="No Nepali at all")
        db.session.add(bare)
        db.session.commit()
        assert bare.to_dict(admin=True)["needs_nepali"] is True


def test_admin_notice_shape_includes_the_stored_nepali(app, fixtures):
    with app.app_context():
        payload = db.session.get(SiteNotice, fixtures["notice_id"]).to_dict(admin=True)
        assert payload["title_ne"] == NEPALI_HEADLINE
        assert payload["title"] == "Admin field notice"
        assert payload["page_paths"] == ["/pricing"]
        # Body Nepali was never written, so the gap is reported.
        assert payload["needs_nepali"] is True


# ── The public shape still withholds it ──────────────────────────────────────

def test_the_public_shape_does_not_leak_targeting_or_translations(app, fixtures):
    """A visitor gets one resolved language and no idea how it was aimed."""
    with app.app_context():
        creative = db.session.get(AdCreative, fixtures["creative_id"])
        public = creative.to_dict()
        for field in ("headline_ne", "body_ne", "page_paths", "countries",
                      "needs_nepali"):
            assert field not in public, f"{field} reached the public payload"

        notice = db.session.get(SiteNotice, fixtures["notice_id"])
        for field in ("title_ne", "body_ne", "page_paths", "needs_nepali"):
            assert field not in notice.to_dict()


def test_the_public_shape_still_resolves_language(app, fixtures):
    with app.app_context():
        creative = db.session.get(AdCreative, fixtures["creative_id"])
        assert creative.to_dict(language="ne")["headline"] == NEPALI_HEADLINE
        assert creative.to_dict(language="en")["headline"] == "Admin field creative"


# ── Through the API, which is what the screens actually call ─────────────────

def test_the_admin_endpoint_returns_the_editable_fields(client, admin_token, fixtures):
    r = client.get(f"/api/ads/campaigns/{fixtures['campaign_id']}/creatives",
                   headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    rows = r.get_json()["items"]
    row = next(c for c in rows if c["id"] == fixtures["creative_id"])
    assert row["headline_ne"] == NEPALI_HEADLINE
    assert row["page_paths"] == ["/pricing"]
    assert "needs_nepali" in row


def test_the_public_endpoint_does_not(client, fixtures):
    r = client.get("/api/ads/serve", query_string={"path": "/", "placement": "admin_fields_slot"})
    assert r.status_code == 200
    for item in r.get_json()["items"]:
        assert "headline_ne" not in item
        assert "countries" not in item


# ── The translation state is three states, not a boolean ─────────────────────

def test_translation_state_distinguishes_partial_from_none(app, fixtures):
    """A boolean reports "no Nepali" for a creative that has a reviewed
    headline, which is the distinction the flag exists to surface."""
    with app.app_context():
        complete = db.session.get(AdCreative, fixtures["creative_id"])
        assert complete.to_dict(admin=True)["nepali_state"] == "complete"
        assert complete.to_dict(admin=True)["needs_nepali"] is False

        partial = AdCreative(campaign_id=fixtures["campaign_id"],
                             placement_id=complete.placement_id,
                             headline="Headline only", headline_ne=NEPALI_HEADLINE)
        none_yet = AdCreative(campaign_id=fixtures["campaign_id"],
                              placement_id=complete.placement_id,
                              headline="Nothing translated")
        db.session.add_all([partial, none_yet])
        db.session.commit()

        assert partial.to_dict(admin=True)["nepali_state"] == "partial"
        assert none_yet.to_dict(admin=True)["nepali_state"] == "none"
        # Both are "needs Nepali", which is why the boolean alone is not
        # enough to drive a list.
        assert partial.to_dict(admin=True)["needs_nepali"] is True
        assert none_yet.to_dict(admin=True)["needs_nepali"] is True


def test_notice_translation_state(app, fixtures):
    with app.app_context():
        notice = db.session.get(SiteNotice, fixtures["notice_id"])
        # Title in Nepali, body never written.
        assert notice.to_dict(admin=True)["nepali_state"] == "partial"


def test_the_public_shape_has_no_translation_state(app, fixtures):
    with app.app_context():
        creative = db.session.get(AdCreative, fixtures["creative_id"])
        assert "nepali_state" not in creative.to_dict()


# ── One response, one shape ──────────────────────────────────────────────────

def test_admin_campaign_detail_nests_admin_creatives(client, admin_token, fixtures):
    """A response that mixed the two shapes left a reader unable to tell
    whether the Nepali columns were absent or merely empty."""
    r = client.get(f"/api/ads/campaigns/{fixtures['campaign_id']}",
                   headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    creatives = r.get_json()["creatives"]
    assert creatives, "fixture campaign should have creatives"
    for creative in creatives:
        assert "headline_ne" in creative
        assert "nepali_state" in creative


def test_public_campaign_shape_still_nests_public_creatives(app, fixtures):
    with app.app_context():
        campaign = db.session.get(AdCampaign, fixtures["campaign_id"])
        for creative in campaign.to_dict(include_creatives=True)["creatives"]:
            assert "headline_ne" not in creative
