"""Telling customers what happened to their parcel.

Ten templates existed, all active, several with Nepali bodies — and nothing
in the application ever asked for one to be sent. A customer who books a
shipment today hears nothing until they telephone the office. For a company
that has spent twenty years answering that phone, a system that stays silent
is not neutral; it is worse than the paper process it replaces.

This module is the one place a notification is decided on and recorded.

**Recording and transmitting are separate, and that is the whole design.**
Every attempt writes a `NotificationLog` row. Whether anything leaves the
building depends on `NOTIFICATIONS_ENABLED`, which is **off by default**.

That default is deliberate and is not timidity. `MAIL_SERVER` points at the
company's real SMTP server, the development database holds real-looking
customer addresses, and switching this on is the moment two decades of
customers start receiving automated mail. So wiring the lifecycle up and
turning sending on are two decisions, taken on different days. With it off,
the office can open the log, see exactly what would have gone to whom, read
the wording, and only then flip it.

Three further rules:

* **A notification never breaks the thing it describes.** Every entry point
  swallows its own errors. A shipment reaching `delivered` must not fail
  because an SMTP server timed out.
* **The same event is never sent twice for the same shipment.** Status can
  be written more than once by retries and by two operators; the log is
  checked before sending.
* **SMS stays behind its existing seam.** `send_sms` returns "not enabled"
  rather than a fabricated success, so nothing here can log an SMS as sent
  that no gateway ever saw.
"""

import json
from datetime import datetime, timedelta

from flask import current_app

from app.extensions import db, mail
from app.models.notification import NotificationLog
from app.models.notification_template import NotificationTemplate

#: Inside this window, the same event for the same shipment is treated as a
#: repeat and skipped. Long enough to cover a retry or two operators pressing
#: the same button; short enough that a genuine re-delivery weeks later is
#: still announced.
REPEAT_WINDOW = timedelta(hours=12)


def sending_enabled():
    """Whether anything may actually be transmitted.

    Off unless the deployment says otherwise. Also forced off under testing,
    so a test suite can never reach a real mail server.
    """
    if current_app.config.get("TESTING"):
        return bool(current_app.config.get("NOTIFICATIONS_ENABLED_IN_TESTS"))
    return bool(current_app.config.get("NOTIFICATIONS_ENABLED", False))


def _mail_configured():
    """Flask-Mail's own view, not the config's.

    The state is frozen at `init_app`, and a message with no sender is
    refused by the library, so a missing default sender means unconfigured
    rather than a delivery failure to be retried.
    """
    try:
        state = mail.state
    except Exception:
        return False
    if state is None:
        return False
    return bool(getattr(state, "server", None)
                and getattr(state, "default_sender", None))


def _recipients_for(template, shipment, user=None):
    """Who this template is addressed to, as (role, address) pairs.

    The template decides whether the sender, the receiver or both hear
    about it. An address the shipment does not carry simply yields nobody —
    plenty of counter bookings have no email for the consignee.
    """
    wants_email = template.channel == NotificationTemplate.EMAIL
    pairs = []

    if shipment is not None:
        if template.send_to_sender:
            address = (shipment.sender_email if wants_email else shipment.sender_phone)
            if not address and wants_email:
                # Fall back to the booking account, but only when that
                # account is the customer. A counter booking is keyed in by
                # staff on behalf of a walk-in, and mailing the clerk every
                # time a parcel moves is noise, not service.
                booked_by = getattr(shipment, "user", None)
                if booked_by is not None and not booked_by.is_admin:
                    address = booked_by.email
            if address:
                pairs.append(("sender", address))
        if template.send_to_receiver:
            address = (shipment.receiver_email if wants_email else shipment.receiver_phone)
            if address:
                pairs.append(("receiver", address))
    elif user is not None:
        address = user.email if wants_email else getattr(user, "phone", None)
        if address:
            pairs.append(("account", address))

    # Two parties can share an address on a counter booking; tell them once.
    seen, unique = set(), []
    for role, address in pairs:
        key = address.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append((role, address))
    return unique


def _already_sent(event, shipment_id, recipient):
    if shipment_id is None:
        return False
    since = datetime.utcnow() - REPEAT_WINDOW
    return db.session.query(
        NotificationLog.query.filter(
            NotificationLog.event == event,
            NotificationLog.shipment_id == shipment_id,
            NotificationLog.recipient == recipient,
            NotificationLog.status.in_((NotificationLog.SENT,
                                        NotificationLog.SUPPRESSED)),
            NotificationLog.sent_at >= since,
        ).exists()
    ).scalar()


def _log(**kwargs):
    entry = NotificationLog(**kwargs)
    db.session.add(entry)
    return entry


def _transmit(template, subject, body, role, address):
    """Hand one message to its channel. Returns (status, detail)."""
    if template.channel == NotificationTemplate.EMAIL:
        if not _mail_configured():
            return NotificationLog.SUPPRESSED, "mail is not configured"
        try:
            from flask_mail import Message
            mail.send(Message(subject=subject or "Moonlight Freight",
                              recipients=[address], body=body))
            return NotificationLog.SENT, None
        except Exception as error:  # noqa: BLE001 - reported, never raised on
            return NotificationLog.FAILED, f"{type(error).__name__}: {error}"[:300]

    if template.channel == NotificationTemplate.SMS:
        from app.notifications.routes import send_sms
        from app.models.notification_template import SmsProvider

        provider = (SmsProvider.query.filter_by(is_active=True, is_primary=True).first()
                    or SmsProvider.query.filter_by(is_active=True).first())
        result = send_sms(provider, address, body)
        if result.get("ok"):
            return NotificationLog.SENT, None
        if result.get("enabled") is False:
            return NotificationLog.SUPPRESSED, result.get("reason", "sms not enabled")
        return NotificationLog.FAILED, str(result.get("reason"))[:300]

    return NotificationLog.SUPPRESSED, f"no transport for channel {template.channel}"


def notify(event, *, shipment=None, user=None, context=None, language="en"):
    """Send (or record) every active template for one event.

    Returns the list of `NotificationLog` rows written, which is what the
    tests assert against. Never raises: the caller is in the middle of
    something more important than this.
    """
    entries = []
    try:
        templates = NotificationTemplate.query.filter_by(
            event=event, is_active=True).all()
        if not templates:
            return entries

        base_context = dict(context or {})
        if shipment is not None:
            base_context.setdefault("tracking_no", shipment.tracking_no)
            base_context.setdefault("status", shipment.status)
            base_context.setdefault("origin", shipment.origin)
            base_context.setdefault("destination", shipment.destination)
            base_context.setdefault("receiver_name", shipment.receiver_name)
            base_context.setdefault("sender_name", shipment.sender_name)

        enabled = sending_enabled()

        for template in templates:
            for role, address in _recipients_for(template, shipment, user=user):
                shipment_id = shipment.id if shipment is not None else None
                if _already_sent(event, shipment_id, address):
                    continue

                subject, body = template.render(base_context, language=language)
                if not body.strip():
                    entries.append(_log(
                        channel=template.channel, status=NotificationLog.SKIPPED,
                        event=event, template_code=template.code,
                        recipient=address, recipient_role=role,
                        shipment_id=shipment_id,
                        detail="template has no body for this language"))
                    continue

                if enabled:
                    status, detail = _transmit(template, subject, body, role, address)
                else:
                    status, detail = (NotificationLog.SUPPRESSED,
                                      "sending is switched off (NOTIFICATIONS_ENABLED)")

                entries.append(_log(
                    channel=template.channel, status=status, event=event,
                    template_code=template.code, recipient=address,
                    recipient_role=role, shipment_id=shipment_id,
                    user_id=(user.id if user is not None else
                             getattr(shipment, "user_id", None) if role == "sender" else None),
                    detail=detail,
                    # The rendered text, so the office can read exactly what
                    # would have gone out before switching sending on.
                    payload=json.dumps({"subject": subject, "body": body},
                                       ensure_ascii=False)[:8000],
                ))

        db.session.commit()
    except Exception:
        # A notification must never take down the operation it describes.
        current_app.logger.exception("Notification dispatch failed for %s", event)
        db.session.rollback()
    return entries


#: Shipment statuses worth telling somebody about, and the event each maps
#: to. Statuses not listed here are internal movements that would be noise
#: in a customer's inbox.
STATUS_EVENTS = {
    "picked_up": "shipment.picked_up",
    "in_transit": "shipment.in_transit",
    "out_for_delivery": "shipment.out_for_delivery",
    "delivered": "shipment.delivered",
    "delivery_attempted": "shipment.delivery_failed",
    "returned": "shipment.returned",
}


def notify_status_change(shipment, previous_status=None):
    """Announce a shipment reaching a status customers care about."""
    event = STATUS_EVENTS.get(shipment.status)
    if not event or shipment.status == previous_status:
        return []
    return notify(event, shipment=shipment)


def notify_shipment_created(shipment):
    return notify("shipment.created", shipment=shipment)
