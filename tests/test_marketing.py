"""Email marketing: permission, not reach.

The properties pinned here are the ones that make this module lawful rather
than merely working, so each of them is a thing somebody could plausibly
"fix" into a breach:

* the public form creates a *pending* contact and nothing in the API —
  admin included — can create a subscribed one;
* the response is identical whether or not the address is already on file,
  so the form is not a membership oracle for the list;
* a repeat subscribe for an address already pending sends nothing, because
  the address may belong to someone the submitter is trying to mailbomb;
* a suppression record survives an unsubscribe and is honoured by the next
  send, with a reason recorded;
* a campaign cannot send without approval, cannot send twice, and cannot
  send a body without an unsubscribe link in it.

Flask-Mail is suppressed under ``TESTING``, so ``mail.record_messages()``
observes the real message object without a socket being opened. The
``mail_on`` / ``mail_off`` fixtures patch Flask-Mail's own state rather than
``app.config``, because the state is built once at ``init_app`` and a later
config edit never reaches it.
"""

import uuid
from datetime import datetime, timedelta

import pytest

from app.extensions import db, mail
from app.models.marketing import EmailCampaign, EmailSend, MarketingContact
from tests.conftest import auth_header

# The namespace is wired up centrally in app/swagger_setup.py, which is not
# ours to edit. Until that line lands the routes do not exist, so register it
# here; the guard makes this a no-op the moment swagger_setup imports it.
import app.swagger_setup as _swagger_setup
from app.extensions import api as _api
from app.marketing.routes import (  # noqa: E402
    MAIL_NOT_CONFIGURED,
    marketing_ns,
    render_campaign_email,
)

if not hasattr(_swagger_setup, "marketing_ns"):
    _api.add_namespace(marketing_ns, path="/api/marketing")


CONSENT_WORDING = ("Email me Moonlight Freight shipping offers and service "
                   "updates. I can unsubscribe at any time.")


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def admin(admin_token):
    return auth_header(admin_token)


@pytest.fixture
def public(app):
    """A browser with no session of its own."""
    return app.test_client()


@pytest.fixture
def mail_on(app):
    """A configured mail server, with no socket behind it.

    ``suppress`` is what ``TESTING`` already sets; it is pinned here so the
    fixture is honest about why nothing leaves the process.
    """
    state = app.extensions["mail"]
    saved = (state.server, state.default_sender, state.suppress)
    state.server = "smtp.moonlight.test"
    state.default_sender = "updates@moonlight.test"
    state.suppress = True
    yield state
    state.server, state.default_sender, state.suppress = saved


@pytest.fixture
def mail_off(app):
    """No mail server at all."""
    state = app.extensions["mail"]
    saved = (state.server, state.default_sender)
    state.server, state.default_sender = None, None
    yield state
    state.server, state.default_sender = saved


def _address(prefix="sub"):
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.test"


def _subscribe(client, address, **extra):
    payload = {"email": address, "consent_text": CONSENT_WORDING,
               "source": "footer"}
    payload.update(extra)
    return client.post("/api/marketing/subscribe", json=payload)


def _contact(address):
    return MarketingContact.query.filter_by(email=address.lower()).first()


def _confirmed_contact(client, address, **extra):
    """Take one address all the way through the double opt-in."""
    assert _subscribe(client, address, **extra).status_code == 202
    contact = _contact(address)
    token = contact.confirm_token
    assert client.get(f"/api/marketing/confirm/{token}").status_code == 200
    return _contact(address)


@pytest.fixture
def campaign(client, admin):
    """An approved campaign whose body does not place the link itself."""
    code = f"camp-{uuid.uuid4().hex[:8]}"
    created = client.post("/api/marketing/campaigns", headers=admin, json={
        "code": code,
        "name": "Dashain offer",
        "subject": "Dashain shipping offer",
        "body_en": "Hello {name}, 15% off parcels to the UK this month.",
        "audience": {"tags": [code]},
    })
    assert created.status_code == 201, created.json
    return created.json


# ── Joining: pending, never subscribed ───────────────────────────────────────

def test_subscribe_creates_a_pending_contact_not_a_subscriber(public, mail_on):
    """The form expresses interest. Only the inbox expresses consent."""
    address = _address()
    with mail.record_messages() as outbox:
        result = _subscribe(public, address)

    assert result.status_code == 202
    contact = _contact(address)
    assert contact is not None
    assert contact.status == MarketingContact.PENDING
    assert contact.confirmed_at is None
    assert contact.is_mailable is False
    assert contact.confirm_token and contact.confirm_sent_at
    # The evidence is captured at the moment of the request, not later.
    assert contact.consent_text == CONSENT_WORDING
    assert contact.source == "footer"
    assert contact.signup_ip_hash

    assert len(outbox) == 1
    assert contact.confirm_token in outbox[0].body


def test_known_and_unknown_addresses_get_the_same_answer(public, mail_on):
    """Any difference here is a membership oracle for the mailing list."""
    fresh = _address()
    known = _address()
    _confirmed_contact(public, known)

    first = _subscribe(public, fresh)
    second = _subscribe(public, known)

    assert first.status_code == second.status_code == 202
    assert first.get_json() == second.get_json()


def test_repeat_subscribe_while_pending_sends_nothing(public, mail_on):
    """One confirmation per address per window, or the form is a mailbomb."""
    address = _address()
    with mail.record_messages() as first_run:
        _subscribe(public, address)
    token = _contact(address).confirm_token

    with mail.record_messages() as second_run:
        for _ in range(3):
            assert _subscribe(public, address).status_code == 202

    assert len(first_run) == 1
    assert second_run == []
    # The live link is untouched, so the one already in their inbox still works.
    assert _contact(address).confirm_token == token


def test_subscribe_after_the_window_issues_one_new_link(public, mail_on):
    """Expiry is what re-opens the door, and it opens it exactly once."""
    address = _address()
    _subscribe(public, address)
    contact = _contact(address)
    original = contact.confirm_token
    contact.confirm_sent_at = datetime.utcnow() - timedelta(
        hours=MarketingContact.CONFIRM_WINDOW_HOURS + 1)
    db.session.commit()

    with mail.record_messages() as outbox:
        _subscribe(public, address)

    assert len(outbox) == 1
    assert _contact(address).confirm_token != original


# ── Confirming ───────────────────────────────────────────────────────────────

def test_confirm_works_once_and_not_twice(public, mail_on):
    """The token is cleared on use, so a replay is indistinguishable from a guess."""
    address = _address()
    _subscribe(public, address)
    token = _contact(address).confirm_token

    first = public.get(f"/api/marketing/confirm/{token}")
    assert first.status_code == 200
    assert first.get_json()["status"] == MarketingContact.SUBSCRIBED

    contact = _contact(address)
    assert contact.confirmed_at is not None
    assert contact.confirm_token is None
    assert contact.unsubscribe_token, "confirming has to mint the way out"
    assert contact.is_mailable is True

    second = public.get(f"/api/marketing/confirm/{token}")
    assert second.status_code == 404


def test_an_expired_confirmation_is_refused_and_says_so(public, mail_on):
    """410 and a readable message, because 'ask again' is actionable."""
    address = _address()
    _subscribe(public, address)
    contact = _contact(address)
    token = contact.confirm_token
    contact.confirm_sent_at = datetime.utcnow() - timedelta(
        hours=MarketingContact.CONFIRM_WINDOW_HOURS + 1)
    db.session.commit()

    result = public.get(f"/api/marketing/confirm/{token}")
    assert result.status_code == 410
    assert "expired" in result.get_json()["message"].lower()
    assert _contact(address).status == MarketingContact.PENDING


# ── Leaving, and staying gone ────────────────────────────────────────────────

def test_one_click_unsubscribe_needs_no_login_and_leaves_a_suppression_record(
        public, mail_on):
    """The row survives: deleting it is how the next import puts them back."""
    address = _address()
    contact = _confirmed_contact(public, address)
    token = contact.unsubscribe_token

    anonymous = public  # no Authorization header anywhere in this test
    result = anonymous.get(f"/api/marketing/unsubscribe/{token}")
    assert result.status_code == 200

    contact = _contact(address)
    assert contact is not None, "the contact must not be deleted"
    assert contact.status == MarketingContact.UNSUBSCRIBED
    assert contact.unsubscribed_at is not None
    assert contact.is_mailable is False
    assert contact.unsubscribe_token == token, "the link stays idempotent"

    again = anonymous.get(f"/api/marketing/unsubscribe/{token}")
    assert again.status_code == 200
    assert again.get_json()["already_unsubscribed"] is True


def test_a_form_post_cannot_resurrect_an_unsubscribed_address(public, mail_on):
    """Only a click from the mailbox brings someone back."""
    address = _address()
    contact = _confirmed_contact(public, address)
    public.get(f"/api/marketing/unsubscribe/{contact.unsubscribe_token}")

    with mail.record_messages() as outbox:
        assert _subscribe(public, address).status_code == 202

    contact = _contact(address)
    assert contact.status == MarketingContact.UNSUBSCRIBED
    assert contact.is_mailable is False
    # A confirmation went out; the status moves only when it is used.
    assert len(outbox) == 1
    public.get(f"/api/marketing/confirm/{contact.confirm_token}")
    assert _contact(address).status == MarketingContact.SUBSCRIBED


def test_a_complained_address_is_never_written_to_again(public, admin, mail_on):
    """A spam complaint outranks a form submission."""
    address = _address()
    contact = _confirmed_contact(public, address)
    suppressed = public.post(f"/api/marketing/contacts/{contact.id}/suppress",
                             headers=admin, json={"reason": "reported as spam",
                                                  "complained": True})
    assert suppressed.status_code == 200

    with mail.record_messages() as outbox:
        assert _subscribe(public, address).status_code == 202
    assert outbox == []
    assert _contact(address).status == MarketingContact.COMPLAINED


def test_unsubscribe_by_email_sends_a_link_rather_than_acting(public, mail_on):
    """Acting on an unauthenticated POST lets anyone unsubscribe anyone."""
    address = _address()
    contact = _confirmed_contact(public, address)

    with mail.record_messages() as outbox:
        result = public.post("/api/marketing/unsubscribe", json={"email": address})

    assert result.status_code == 202
    assert _contact(address).status == MarketingContact.SUBSCRIBED, \
        "the POST must not unsubscribe anybody by itself"
    assert len(outbox) == 1
    assert contact.unsubscribe_token in outbox[0].body


# ── No endpoint creates a subscriber ─────────────────────────────────────────

def test_admin_cannot_set_a_contact_subscribed(public, admin, mail_on):
    address = _address()
    _subscribe(public, address)
    contact = _contact(address)

    result = public.put(f"/api/marketing/contacts/{contact.id}", headers=admin,
                        json={"status": MarketingContact.SUBSCRIBED})
    assert result.status_code == 403
    assert _contact(address).status == MarketingContact.PENDING

    allowed = public.put(f"/api/marketing/contacts/{contact.id}", headers=admin,
                         json={"tags": ["diaspora-uk"], "language": "ne"})
    assert allowed.status_code == 200
    assert allowed.get_json()["tags"] == ["diaspora-uk"]


def test_import_creates_pending_contacts_and_records_the_claimed_source(
        public, admin, mail_on):
    """A list without proof of consent is a list of people who still have to confirm."""
    addresses = [_address("imp") for _ in range(3)]
    with mail.record_messages() as outbox:
        result = public.post("/api/marketing/contacts/import", headers=admin, json={
            "contacts": [{"email": a} for a in addresses],
            "source": "2024 trade fair sign-up sheet",
            "consent_text": "Tick to receive Moonlight Freight updates",
        })

    assert result.status_code == 201
    assert result.get_json()["created"] == 3
    assert outbox == [], "an import must not mail anyone by default"
    for address in addresses:
        contact = _contact(address)
        assert contact.status == MarketingContact.PENDING
        assert contact.is_mailable is False
        assert contact.source == "import:2024 trade fair sign-up sheet"


# ── Sending ──────────────────────────────────────────────────────────────────

def test_a_campaign_cannot_send_without_approval(public, admin, campaign, mail_on):
    with mail.record_messages() as outbox:
        result = public.post(f"/api/marketing/campaigns/{campaign['id']}/send",
                             headers=admin, json={"confirm": campaign["code"]})
    assert result.status_code == 409
    assert "approved" in result.get_json()["message"].lower()
    assert outbox == []
    assert EmailSend.query.filter_by(campaign_id=campaign["id"]).count() == 0


def test_sending_needs_the_campaign_code_typed_back(public, admin, campaign, mail_on):
    public.post(f"/api/marketing/campaigns/{campaign['id']}/approve", headers=admin)
    result = public.post(f"/api/marketing/campaigns/{campaign['id']}/send",
                         headers=admin, json={"confirm": True})
    assert result.status_code == 400


def test_editing_the_body_withdraws_the_approval(public, admin, campaign):
    approved = public.post(f"/api/marketing/campaigns/{campaign['id']}/approve",
                           headers=admin)
    assert approved.status_code == 200
    assert approved.get_json()["is_approved"] is True

    edited = public.put(f"/api/marketing/campaigns/{campaign['id']}", headers=admin,
                        json={"body_en": "Something else entirely."})
    assert edited.status_code == 200
    assert edited.get_json()["is_approved"] is False, \
        "approving a draft then rewriting it would make approval meaningless"


def test_a_send_skips_suppressed_contacts_with_a_reason(public, admin, campaign,
                                                        mail_on):
    """A thin send has to be explainable, not just small."""
    tag = campaign["code"]
    good = _confirmed_contact(public, _address("good"))
    gone = _confirmed_contact(public, _address("gone"))
    pending_address = _address("wait")
    _subscribe(public, pending_address)
    pending = _contact(pending_address)

    public.get(f"/api/marketing/unsubscribe/{gone.unsubscribe_token}")
    for contact in (good, gone, pending):
        contact.tags = [tag]
    db.session.commit()

    public.put(f"/api/marketing/campaigns/{campaign['id']}", headers=admin, json={
        "audience": {"tags": [tag], "status": [MarketingContact.SUBSCRIBED,
                                               MarketingContact.UNSUBSCRIBED,
                                               MarketingContact.PENDING]},
    })
    public.post(f"/api/marketing/campaigns/{campaign['id']}/approve", headers=admin)

    with mail.record_messages() as outbox:
        sent = public.post(f"/api/marketing/campaigns/{campaign['id']}/send",
                           headers=admin, json={"confirm": campaign["code"]})
    assert sent.status_code == 200

    report = public.get(f"/api/marketing/campaigns/{campaign['id']}/report",
                        headers=admin).get_json()
    assert report["delivered"] == 1
    assert report["skipped"] == 2
    assert report["skipped_by_reason"] == {"unsubscribed": 1, "unconfirmed": 1}
    assert [m.recipients for m in outbox] == [[good.email]]

    skipped = EmailSend.query.filter_by(campaign_id=campaign["id"],
                                        contact_id=gone.id).one()
    assert skipped.status == EmailSend.SKIPPED
    assert skipped.skip_reason == "unsubscribed"


def test_every_send_body_carries_a_working_unsubscribe_link(public, admin,
                                                            campaign, mail_on):
    tag = campaign["code"]
    contact = _confirmed_contact(public, _address("link"))
    contact.tags = [tag]
    db.session.commit()
    public.post(f"/api/marketing/campaigns/{campaign['id']}/approve", headers=admin)

    with mail.record_messages() as outbox:
        public.post(f"/api/marketing/campaigns/{campaign['id']}/send",
                    headers=admin, json={"confirm": campaign["code"]})

    assert len(outbox) == 1
    body = outbox[0].body
    token = _contact(contact.email).unsubscribe_token
    assert f"/api/marketing/unsubscribe/{token}" in body

    # And the link in the body actually unsubscribes, with no login.
    start = body.index(f"/api/marketing/unsubscribe/{token}")
    path = body[start:].split()[0]
    assert public.get(path).status_code == 200
    assert _contact(contact.email).status == MarketingContact.UNSUBSCRIBED


def test_the_unsubscribe_footer_is_added_even_if_the_body_forgets_it():
    """The link is a guarantee of the renderer, not of whoever wrote the copy."""
    campaign = EmailCampaign(code="x", name="x", subject="Hi {name}",
                             body_en="No footer here.")
    contact = MarketingContact(email="a@example.test", name="Asha", language="en")
    subject, body = render_campaign_email(campaign, contact, "https://x.test/u/abc")
    assert subject == "Hi Asha"
    assert "https://x.test/u/abc" in body


def test_re_running_a_send_does_not_send_twice(public, admin, campaign, mail_on):
    """The unique constraint says once; this makes it true in practice."""
    tag = campaign["code"]
    contact = _confirmed_contact(public, _address("once"))
    contact.tags = [tag]
    db.session.commit()
    public.post(f"/api/marketing/campaigns/{campaign['id']}/approve", headers=admin)

    with mail.record_messages() as first_run:
        public.post(f"/api/marketing/campaigns/{campaign['id']}/send",
                    headers=admin, json={"confirm": campaign["code"]})
    with mail.record_messages() as second_run:
        repeat = public.post(f"/api/marketing/campaigns/{campaign['id']}/send",
                             headers=admin, json={"confirm": campaign["code"]})

    assert len(first_run) == 1
    assert second_run == [], "a re-run resumes; it does not resend"
    assert repeat.get_json()["already_handled"] >= 1
    assert EmailSend.query.filter_by(campaign_id=campaign["id"],
                                     contact_id=contact.id).count() == 1
    report = public.get(f"/api/marketing/campaigns/{campaign['id']}/report",
                        headers=admin).get_json()
    assert report["delivered"] == 1


def test_an_unconfigured_mail_server_is_recorded_not_faked(public, admin,
                                                           campaign, mail_on,
                                                           mail_off):
    """'Not sent' is a result. Counting it as delivered would be a lie in a report."""
    tag = campaign["code"]
    # mail_on runs first so the double opt-in can complete, then mail_off
    # takes the server away for the campaign itself.
    contact = MarketingContact(email=_address("dark"), status=MarketingContact.SUBSCRIBED,
                               confirmed_at=datetime.utcnow(), tags=[tag],
                               unsubscribe_token=MarketingContact.new_token())
    db.session.add(contact)
    db.session.commit()
    public.post(f"/api/marketing/campaigns/{campaign['id']}/approve", headers=admin)

    result = public.post(f"/api/marketing/campaigns/{campaign['id']}/send",
                         headers=admin, json={"confirm": campaign["code"]})
    assert result.status_code == 200
    assert result.get_json()["mail_configured"] is False

    row = EmailSend.query.filter_by(campaign_id=campaign["id"],
                                    contact_id=contact.id).one()
    assert row.status == EmailSend.FAILED
    assert row.error == MAIL_NOT_CONFIGURED
    assert row.sent_at is None

    report = public.get(f"/api/marketing/campaigns/{campaign['id']}/report",
                        headers=admin).get_json()
    assert report["delivered"] == 0
    assert report["failed"] == 1


def test_the_report_refuses_to_invent_opens_and_clicks(public, admin, campaign):
    report = public.get(f"/api/marketing/campaigns/{campaign['id']}/report",
                        headers=admin).get_json()
    assert report["open_rate"] is None
    assert report["click_rate"] is None
    assert "not measured" in report["tracking_note"]


def test_an_unsubscribe_from_a_campaign_is_attributed_to_it(public, admin,
                                                            campaign, mail_on):
    tag = campaign["code"]
    contact = _confirmed_contact(public, _address("attr"))
    contact.tags = [tag]
    db.session.commit()
    public.post(f"/api/marketing/campaigns/{campaign['id']}/approve", headers=admin)
    public.post(f"/api/marketing/campaigns/{campaign['id']}/send",
                headers=admin, json={"confirm": campaign["code"]})

    token = _contact(contact.email).unsubscribe_token
    public.get(f"/api/marketing/unsubscribe/{token}?c={campaign['id']}")

    report = public.get(f"/api/marketing/campaigns/{campaign['id']}/report",
                        headers=admin).get_json()
    assert report["unsubscribes"] == 1


# ── Admin listing shape ──────────────────────────────────────────────────────

def test_contact_list_uses_the_items_envelope_and_filters(public, admin, mail_on):
    tag = f"seg-{uuid.uuid4().hex[:8]}"
    contact = _confirmed_contact(public, _address("seg"))
    contact.tags = [tag]
    contact.country = "GB"
    db.session.commit()

    listed = public.get(f"/api/marketing/contacts?tag={tag}", headers=admin)
    body = listed.get_json()
    assert listed.status_code == 200
    assert set(body) >= {"items", "total", "page", "per_page", "pages"}
    assert body["total"] == 1
    assert body["items"][0]["email"] == contact.email

    assert public.get(f"/api/marketing/contacts?tag={tag}&country=NP",
                      headers=admin).get_json()["total"] == 0


def test_contact_detail_shows_the_consent_evidence(public, admin, mail_on):
    address = _address("evid")
    contact = _confirmed_contact(public, address)
    body = public.get(f"/api/marketing/contacts/{contact.id}",
                      headers=admin).get_json()
    assert body["consent_text"] == CONSENT_WORDING
    assert body["source"] == "footer"
    assert body["confirm_ip_hash"]


def test_marketing_admin_endpoints_reject_an_ordinary_visitor(public):
    assert public.get("/api/marketing/contacts").status_code in (401, 422)
    assert public.get("/api/marketing/campaigns").status_code in (401, 422)
