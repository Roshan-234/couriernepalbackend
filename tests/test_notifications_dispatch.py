"""Customers get told what happened to their parcel.

Ten templates existed, all active, and nothing ever asked for one. A
customer who booked a shipment heard nothing until they telephoned the
office — a downgrade on the paper process this replaces.

The tests fall into three groups, and the middle one is the important one:

* the lifecycle actually dispatches;
* **nothing is transmitted until somebody switches it on**, while being
  recorded regardless, so the office can read what would have gone out;
* a notification can never damage the operation it describes.
"""

import pytest

from app.extensions import db
from app.models.notification import NotificationLog
from app.models.notification_template import NotificationTemplate
from app.models.shipment import Shipment
from app.notifications import dispatch
from tests.conftest import auth_header


@pytest.fixture(autouse=True)
def templates(app):
    """The two templates these tests rely on, if seeding has not run."""
    with app.app_context():
        wanted = [
            ("test_email_created", "shipment.created", NotificationTemplate.EMAIL,
             "Booking {tracking_no}", "We have your parcel {tracking_no}.", True, False),
            ("test_email_delivered", "shipment.delivered", NotificationTemplate.EMAIL,
             "Delivered {tracking_no}", "{tracking_no} was delivered.", True, True),
            ("test_email_picked_up", "shipment.picked_up", NotificationTemplate.EMAIL,
             "Collected {tracking_no}", "We have collected {tracking_no}.", True, False),
        ]
        for code, event, channel, subject, body, to_sender, to_receiver in wanted:
            if not NotificationTemplate.query.filter_by(code=code).first():
                db.session.add(NotificationTemplate(
                    code=code, name=code, event=event, channel=channel,
                    subject=subject, body_en=body, is_active=True,
                    send_to_sender=to_sender, send_to_receiver=to_receiver))
        db.session.commit()


@pytest.fixture
def booking(client, admin_token):
    payload = {
        "origin": "Kathmandu", "destination": "Pokhara",
        "origin_country": "Nepal", "destination_country": "Nepal",
        "origin_district": "Kathmandu", "destination_district": "Kaski",
        "sender_name": "Sita Sender", "receiver_name": "Ram Receiver",
        "sender_phone": "+9779800000000", "receiver_phone": "+9779800000001",
        "sender_email": "sender@example.com", "receiver_email": "receiver@example.com",
        "sender_address": "Nayabazar", "receiver_address": "Lakeside",
        "total_weight_kg": 3.0,
    }
    r = client.post("/api/shipments/", json=payload, headers=auth_header(admin_token))
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()


def _logs(event=None, shipment_id=None):
    query = NotificationLog.query
    if event:
        query = query.filter_by(event=event)
    if shipment_id:
        query = query.filter_by(shipment_id=shipment_id)
    return query.all()


# ── The lifecycle dispatches ─────────────────────────────────────────────────

def test_booking_a_shipment_notifies(app, booking):
    with app.app_context():
        entries = _logs("shipment.created", booking["id"])
        assert entries, "a booking told nobody"
        assert any(e.recipient == "sender@example.com" for e in entries)


def test_delivering_notifies_the_receiver_who_has_no_account(app, client, admin_token, booking):
    """The consignee is the person waiting, and has no login."""
    shipment_id = booking["id"]
    for status in ("picked_up", "in_transit", "out_for_delivery", "delivered"):
        client.put(f"/api/shipments/{shipment_id}",
                   json={"status": status}, headers=auth_header(admin_token))

    with app.app_context():
        entries = _logs("shipment.delivered", shipment_id)
        assert entries, "delivery told nobody"
        receivers = [e for e in entries if e.recipient_role == "receiver"]
        assert receivers, "the receiver was not told"
        assert receivers[0].user_id is None, "the receiver has no account by design"


def test_a_tracking_event_that_moves_status_notifies(app, client, admin_token, booking):
    shipment_id = booking["id"]
    before = len(_logs(shipment_id=shipment_id))
    client.post(f"/api/tracking/{booking['tracking_no']}/events",
                json={"status": "picked_up", "location": "Kathmandu",
                      "description": "Collected"},
                headers=auth_header(admin_token))
    with app.app_context():
        assert len(_logs(shipment_id=shipment_id)) > before


def test_an_internal_status_does_not_pester_the_customer(app, client, admin_token, booking):
    """Not every movement is worth an SMS."""
    with app.app_context():
        assert "pending" not in dispatch.STATUS_EVENTS
        assert "at_hub" not in dispatch.STATUS_EVENTS


def test_the_same_event_is_not_sent_twice(app, client, admin_token, booking):
    """Status can be written again by a retry or a second operator."""
    shipment_id = booking["id"]
    for _ in range(3):
        client.put(f"/api/shipments/{shipment_id}/status",
                   json={"status": "picked_up"}, headers=auth_header(admin_token))
    with app.app_context():
        sender_entries = [e for e in _logs("shipment.picked_up", shipment_id)
                          if e.recipient_role == "sender"]
        assert len(sender_entries) <= 1, "the customer was told the same thing twice"


# ── Nothing is transmitted until somebody switches it on ─────────────────────

def test_nothing_is_transmitted_by_default(app, booking):
    """The load-bearing test.

    MAIL_SERVER points at the company's real mail server and the customer
    list is twenty years old. Wiring the lifecycle up and starting to send
    are two separate decisions, and this is what keeps them separate.
    """
    with app.app_context():
        assert dispatch.sending_enabled() is False
        entries = _logs("shipment.created", booking["id"])
        assert entries
        assert all(e.status == NotificationLog.SUPPRESSED for e in entries)
        assert all(e.was_transmitted is False for e in entries)


def test_the_suppressed_message_is_still_readable(app, booking):
    """The office must be able to read what would have gone out."""
    with app.app_context():
        # Scoped to this booking: the session-scoped database carries rows
        # from every other test in this file.
        entry = _logs("shipment.created", booking["id"])[0]
        assert entry.payload and booking["tracking_no"] in entry.payload
        assert entry.detail and "switched off" in entry.detail


def test_sending_stays_off_under_testing_even_if_configured(app):
    """A test suite must not be one config flag away from real email."""
    with app.app_context():
        app.config["NOTIFICATIONS_ENABLED"] = True
        try:
            assert dispatch.sending_enabled() is False
        finally:
            app.config["NOTIFICATIONS_ENABLED"] = False


def test_sms_is_never_logged_as_sent(app, booking):
    """SMS is a seam with no gateway behind it."""
    with app.app_context():
        sms = [e for e in NotificationLog.query.filter_by(
            channel=NotificationTemplate.SMS).all()]
        assert all(e.status != NotificationLog.SENT for e in sms), (
            "an SMS was recorded as sent with no gateway configured")


# ── A notification never damages the operation ───────────────────────────────

def test_a_dispatch_failure_does_not_break_the_status_change(
        app, client, admin_token, booking, monkeypatch):
    """The parcel is more important than the message about it."""
    def explode(*args, **kwargs):
        raise RuntimeError("template store unavailable")

    monkeypatch.setattr(dispatch.NotificationTemplate, "query",
                        property(lambda self: explode()))

    r = client.put(f"/api/shipments/{booking['id']}",
                   json={"status": "picked_up"}, headers=auth_header(admin_token))
    assert r.status_code == 200, "a notification fault lost the status change"

    with app.app_context():
        shipment = db.session.get(Shipment, booking["id"])
        assert shipment.status == "picked_up"


def test_notify_returns_quietly_for_an_unknown_event(app, booking):
    with app.app_context():
        assert dispatch.notify("nothing.like.this") == []


def test_a_shipment_with_no_addresses_notifies_nobody(app, client, admin_token):
    """A counter booking often has no email for either party."""
    r = client.post("/api/shipments/", json={
        "origin": "Kathmandu", "destination": "Pokhara",
        "origin_country": "Nepal", "destination_country": "Nepal",
        "sender_name": "Walk In", "receiver_name": "Collect",
        "sender_phone": "", "receiver_phone": "",
        "sender_address": "Counter", "receiver_address": "Counter",
        "total_weight_kg": 1.0,
    }, headers=auth_header(admin_token))
    assert r.status_code in (200, 201)
    with app.app_context():
        assert _logs("shipment.created", r.get_json()["id"]) == []


def test_one_address_on_both_sides_is_told_once(app, client, admin_token):
    """Counter staff often enter the shop's own address for both parties."""
    shared = "same@example.com"
    r = client.post("/api/shipments/", json={
        "origin": "Kathmandu", "destination": "Pokhara",
        "origin_country": "Nepal", "destination_country": "Nepal",
        "sender_name": "A", "receiver_name": "B",
        "sender_email": shared, "receiver_email": shared,
        "sender_phone": "+9779800000002", "receiver_phone": "+9779800000003",
        "sender_address": "X", "receiver_address": "Y",
        "total_weight_kg": 1.0,
    }, headers=auth_header(admin_token))
    shipment_id = r.get_json()["id"]
    for status in ("picked_up", "in_transit", "out_for_delivery", "delivered"):
        client.put(f"/api/shipments/{shipment_id}",
                   json={"status": status}, headers=auth_header(admin_token))
    with app.app_context():
        delivered = [e for e in _logs("shipment.delivered", shipment_id)
                     if e.channel == NotificationTemplate.EMAIL
                     and e.recipient == shared]
        assert len(delivered) == 1, f"told {len(delivered)} times at one address"


# ── Placeholders are filled, or removed and reported ─────────────────────────

def test_a_real_template_renders_with_no_placeholders_left(app, client, admin_token):
    """The defect this guards against shipped once already.

    The dispatcher supplied six values while the seeded templates
    reference eleven, so a customer would have been sent the literal text
    "Track it at {tracking_url}".
    """
    from app.data.seed_logistics import TEMPLATES
    from app.notifications.dispatch import shipment_context, _finalise

    with app.app_context():
        # Seed the real wording if this database does not have it.
        for code, event, channel, subject, body_en, body_ne in TEMPLATES:
            if not NotificationTemplate.query.filter_by(code=code).first():
                db.session.add(NotificationTemplate(
                    code=code, name=code, event=event, channel=channel,
                    subject=subject, body_en=body_en, body_ne=body_ne,
                    is_active=False))
        db.session.commit()

        shipment = Shipment.query.first()
        assert shipment is not None
        context = {**shipment_context(shipment), "customer_name": "Test Customer"}

        unfilled = {}
        for template in NotificationTemplate.query.all():
            subject, body = template.render(context)
            _, missing_subject = _finalise(subject)
            _, missing_body = _finalise(body)
            gaps = sorted(set(missing_subject) | set(missing_body))
            # Placeholders only an event payload can supply are fair.
            gaps = [g for g in gaps if g not in {
                "otp", "reason", "next_date", "remittance_no", "net_payable",
                "pickup_no", "scheduled_date", "time_slot", "collected_count",
                "amount", "invoice_no"}]
            if gaps:
                unfilled[template.code] = gaps

        assert not unfilled, (
            f"templates reference placeholders the dispatcher never fills: {unfilled}")


def test_an_unfilled_placeholder_is_stripped_not_sent(app):
    from app.notifications.dispatch import _finalise

    cleaned, missing = _finalise("Rider: {rider_name}, {rider_phone}. Thanks.")
    assert missing == ["rider_name", "rider_phone"]
    assert "{" not in cleaned and "}" not in cleaned
    # And it does not leave a trail of double spaces behind.
    assert "  " not in cleaned


def test_the_receiver_is_addressed_by_their_own_name(app, client, admin_token, booking):
    shipment_id = booking["id"]
    for status in ("picked_up", "in_transit", "out_for_delivery", "delivered"):
        client.put(f"/api/shipments/{shipment_id}",
                   json={"status": status}, headers=auth_header(admin_token))
    with app.app_context():
        receiver_rows = [e for e in _logs("shipment.delivered", shipment_id)
                         if e.recipient_role == "receiver"]
        assert receiver_rows
        assert "Sita Sender" not in (receiver_rows[0].payload or ""), (
            "the receiver was addressed as the sender")


def test_a_parcel_with_no_rider_still_reads_as_a_sentence(app):
    """"Rider: , " is not something to send a customer.

    A parcel can be marked out for delivery before a named rider is on it,
    and an empty substitution leaves the template's own punctuation behind.
    """
    from app.notifications.dispatch import shipment_context

    with app.app_context():
        shipment = Shipment.query.filter_by(rider_id=None).first()
        if shipment is None:
            pytest.skip("no rider-less shipment in this database")
        context = shipment_context(shipment)
        assert context["rider_name"].strip()
        assert context["rider_phone"].strip()

        rendered = ("Rider: {rider_name}, {rider_phone}"
                    .replace("{rider_name}", context["rider_name"])
                    .replace("{rider_phone}", context["rider_phone"]))
        assert not rendered.rstrip().endswith(",")
        assert ": ," not in rendered
