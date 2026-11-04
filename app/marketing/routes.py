"""Email marketing: getting permission, keeping it, and proving both.

The model (``app/models/marketing.py``) says consent has to be provable and
unsubscribing has to last. These routes are where that is enforced, and five
decisions here are deliberate rather than incidental:

* **Nothing becomes a subscriber without a click from the inbox.** The public
  form only ever creates a ``pending`` contact. No endpoint in this module —
  admin included — can set a contact to ``subscribed`` directly. The only
  transition into that state is :meth:`MarketingContact.confirm`, reached by
  presenting a token that was mailed to the address itself.
* **The subscribe endpoint is a weapon if it resends.** Anyone can type a
  stranger's address into a public form. One confirmation email per address
  per confirmation window is the cap; see :class:`Subscribe`.
* **The subscribe response never reveals whether the address is known.**
  A different answer for "already subscribed" turns the form into a
  membership oracle for the mailing list, which is exactly the lookup a
  scraper wants.
* **No open pixel, no click rewriting.** The model docstring explains why.
  The only query parameter added to a link in an outbound mail is the
  campaign id on the *unsubscribe* link, so a report can say how many people
  left because of a given mailing. It records the recipient's own act of
  leaving, not that they read anything.
* **A send that cannot send says so.** With no mail server configured, each
  attempt is written down as a failure carrying
  :data:`MAIL_NOT_CONFIGURED`. It is never recorded as delivered.
"""

from datetime import datetime, timedelta

from flask import current_app, request
from flask_jwt_extended import get_jwt_identity
from flask_mail import Message
from flask_restx import Namespace, Resource, fields
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import BadRequest

from app.auth.utils import require_roles
from app.extensions import db, limiter, mail
from app.models.marketing import EmailCampaign, EmailSend, MarketingContact
from app.net import rate_limit_key
from app.validation import json_object, pagination, text
from app.visitor import request_fingerprint

marketing_ns = Namespace("marketing", description="Email marketing and double opt-in")


#: Written into ``EmailSend.error`` when there is no mail server to hand the
#: message to. Spelled out rather than left as a bare exception string because
#: a report has to be able to distinguish "we never tried" from "the MTA
#: refused it" — only the first is safe to retry.
MAIL_NOT_CONFIGURED = "not sent - mail not configured"

#: Appended to any campaign body that does not already place the link itself.
UNSUBSCRIBE_NOTICE = (
    "\n\n-- \n"
    "You are receiving this because you confirmed a subscription to Moonlight "
    "Freight updates.\n"
    "Unsubscribe at any time: {unsubscribe_url}\n"
)

#: A test send has no contact and therefore no token. The body still carries a
#: line where the link goes, so a reviewer can see the footer is there.
TEST_UNSUBSCRIBE_PLACEHOLDER = "[unsubscribe link - generated per recipient on the real send]"

#: Statuses an audience defaults to when a campaign does not name any. A
#: campaign with no audience filter mails confirmed subscribers and nobody
#: else; it never means "everyone in the table".
DEFAULT_AUDIENCE_STATUS = (MarketingContact.SUBSCRIBED,)

#: Why a contact in the audience was passed over. Stored on the send row so a
#: thin campaign can be explained without re-deriving the audience.
SKIP_UNCONFIRMED = "unconfirmed"
SKIP_UNSUBSCRIBED = "unsubscribed"
SKIP_BOUNCED = "bounced"
SKIP_COMPLAINED = "complained"
SKIP_NO_ADDRESS = "no_address"


# ── Small helpers ────────────────────────────────────────────────────────────

def _email_address(value, field="email"):
    """Normalise and sanity-check an address.

    Deliberately not an RFC 5322 parser. The only authority on whether an
    address exists is whether the confirmation mail arrives, so this rejects
    the obviously-wrong and lets the double opt-in do the rest.
    """
    value = text(value, field, 200, required=True).lower()
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain or " " in value:
        raise BadRequest(f"{field} must be a valid email address")
    return value


def _public_link(path):
    """A link a recipient can click.

    The backend has no public hostname of its own: the site is served by the
    frontend, which proxies ``/api`` same-origin (see ``app/net.py``). Pointing
    confirm and unsubscribe links at that proxy means they work today, before
    any dedicated page exists, and a nicer page can be put in front later
    without reissuing a single token.
    """
    base = current_app.config.get("FRONTEND_URL") or ""
    return f"{base}/api/marketing{path}"


def unsubscribe_url(contact, campaign=None):
    """The one-click unsubscribe link for a contact.

    ``?c=`` names the campaign so ``/campaigns/<id>/report`` can say how many
    people left after a given mailing. It is attached to this link only, and
    it is read only when the recipient acts on it.
    """
    if not contact.unsubscribe_token:
        contact.unsubscribe_token = MarketingContact.new_token()
    link = _public_link(f"/unsubscribe/{contact.unsubscribe_token}")
    return f"{link}?c={campaign.id}" if campaign is not None else link


def _mail_is_configured():
    """Whether there is somewhere to hand a message to, and a name to send as.

    Asks Flask-Mail's own state rather than the raw config, because that is
    what would actually do the sending: the state is built once at
    ``init_app`` and a later config edit does not reach it. A missing sender
    counts as unconfigured — a message with no From address is refused by the
    library before it reaches a socket, and calling that a delivery failure
    would be misleading.

    ``MAIL_SUPPRESS_SEND`` (set under testing) is deliberately *not* treated
    as unconfigured: Flask-Mail still builds and signals the message, it just
    never opens a socket. That is the seam the tests use.
    """
    state = current_app.extensions.get("mail")
    return bool(state is not None and state.server and state.default_sender)


def _deliver(recipient, subject, body):
    """Hand one message to Flask-Mail. Returns ``(ok, error)``.

    Never raises: a campaign of four hundred must not stop at the first
    rejected address. The outcome is written to the send row instead.
    """
    if not _mail_is_configured():
        return False, MAIL_NOT_CONFIGURED
    try:
        mail.send(Message(subject=subject, recipients=[recipient], body=body))
        return True, None
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        current_app.logger.warning("Marketing send failed: %s", type(exc).__name__)
        return False, f"{type(exc).__name__}: {exc}"[:300]


def render_campaign_email(campaign, contact, link):
    """Build the subject and body for one recipient.

    Placeholders follow ``NotificationTemplate.render``: ``{name}``,
    ``{email}`` and ``{unsubscribe_url}``. If the body does not place the
    unsubscribe link itself, the footer is appended — the link is a guarantee
    of this function, not of whoever wrote the campaign.
    """
    language = (contact.language or "en").lower()
    body = (campaign.body_ne if language == "ne" and campaign.body_ne
            else campaign.body_en) or ""
    subject = campaign.subject or ""
    context = {
        "name": contact.name or "",
        "email": contact.email or "",
        "unsubscribe_url": link,
    }
    for key, value in context.items():
        token = "{" + key + "}"
        body = body.replace(token, value)
        subject = subject.replace(token, value)
    if link not in body:
        body += UNSUBSCRIBE_NOTICE.format(unsubscribe_url=link)
    return subject, body


def send_confirmation_email(contact):
    """Issue a fresh confirmation token and mail the link. Returns ``(ok, error)``.

    The caller decides *whether* this is allowed to happen; this function only
    decides what it looks like. It always mints a new token, so an old link
    stops working the moment a new one is sent.
    """
    contact.confirm_token = MarketingContact.new_token()
    contact.confirm_sent_at = datetime.utcnow()
    link = _public_link(f"/confirm/{contact.confirm_token}")
    greeting = f"Hello {contact.name}," if contact.name else "Hello,"
    body = (
        f"{greeting}\n\n"
        "Someone - we hope you - asked for Moonlight Freight updates at this "
        "address. Nothing is sent until you confirm:\n\n"
        f"{link}\n\n"
        f"The link is good for {MarketingContact.CONFIRM_WINDOW_HOURS} hours. "
        "If this was not you, ignore this message: without the click above you "
        "stay off the list, and we will not write again.\n"
    )
    return _deliver(contact.email, "Confirm your Moonlight Freight subscription", body)


def _confirmation_in_flight(contact):
    """True while a confirmation link already sent to this address is still live."""
    if not contact.confirm_token or not contact.confirm_sent_at:
        return False
    window = timedelta(hours=MarketingContact.CONFIRM_WINDOW_HOURS)
    return contact.confirm_sent_at + window > datetime.utcnow()


def _current_user_id():
    return int(get_jwt_identity())


def _tags_argument(value, field="tags"):
    if not isinstance(value, list) or any(not isinstance(t, str) for t in value):
        raise BadRequest(f"{field} must be a list of strings")
    cleaned = sorted({t.strip()[:40] for t in value if t.strip()})
    if len(cleaned) > 25:
        raise BadRequest(f"{field} may hold at most 25 labels")
    return cleaned


def _subscribe_email_key():
    """Rate-limit bucket for the address being submitted, not the submitter.

    Per-IP limiting alone does not protect the victim of a mailbomb: the
    attacker moves address, the target stays the same.
    """
    try:
        payload = request.get_json(silent=True) or {}
        address = str(payload.get("email", ""))[:200].strip().lower()
    except Exception:  # noqa: BLE001 - a malformed body is somebody else's 400
        address = ""
    return f"subscribe:{address or rate_limit_key()}"


# ── Swagger models ───────────────────────────────────────────────────────────

subscribe_input = marketing_ns.model("SubscribeInput", {
    "email": fields.String(required=True),
    "name": fields.String(),
    "language": fields.String(enum=["en", "ne"], default="en"),
    "country": fields.String(description="ISO 3166-1 alpha-2"),
    "source": fields.String(description="Where the form was, e.g. 'footer', 'checkout'"),
    "consent_text": fields.String(
        required=True,
        description="The exact wording shown next to the box the person ticked",
    ),
})

unsubscribe_input = marketing_ns.model("UnsubscribeRequestInput", {
    "email": fields.String(required=True),
})

contact_update_input = marketing_ns.model("MarketingContactUpdate", {
    "tags": fields.List(fields.String),
    "language": fields.String(enum=["en", "ne"]),
})

import_input = marketing_ns.model("MarketingImportInput", {
    "contacts": fields.List(fields.Raw, required=True,
                            description="[{email, name?, language?, country?, tags?}]"),
    "source": fields.String(required=True, description="Where the list came from"),
    "consent_text": fields.String(required=True,
                                  description="The consent the list is claimed to carry"),
    "send_confirmation": fields.Boolean(default=False),
})

campaign_input = marketing_ns.model("EmailCampaignInput", {
    "code": fields.String(required=True),
    "name": fields.String(required=True),
    "subject": fields.String(required=True),
    "preheader": fields.String(),
    "body_en": fields.String(),
    "body_ne": fields.String(),
    "audience": fields.Raw(description="{tags: [...], status: [...], country: '..'}"),
    "utm_campaign": fields.String(),
    "ad_campaign_id": fields.Integer(),
    "scheduled_for": fields.String(description="ISO 8601"),
})

send_input = marketing_ns.model("EmailCampaignSendInput", {
    "confirm": fields.String(required=True,
                             description="The campaign's own code, typed back"),
})


# ── Public: joining and leaving ──────────────────────────────────────────────

@marketing_ns.route("/subscribe")
class Subscribe(Resource):
    @marketing_ns.doc("marketing_subscribe")
    @marketing_ns.expect(subscribe_input)
    @marketing_ns.response(202, "Accepted - a confirmation may have been sent")
    @limiter.limit("5 per hour", key_func=rate_limit_key)
    @limiter.limit("3 per day", key_func=_subscribe_email_key)
    def post(self):
        """Ask to join the list. Creates a *pending* contact and nothing more.

        **The answer is the same whatever we already hold.** New address,
        pending address, confirmed subscriber, suppressed address: one
        response body, one status code. Any difference between them would let
        a caller test membership of the mailing list one address at a time.

        **Resend policy: one confirmation per address per confirmation
        window.** This form accepts an address typed by whoever is at the
        keyboard, which may not be the owner of the mailbox. Resending on
        every submission turns it into a mailbomb aimed at a third party, so
        while an unexpired confirmation link is outstanding a repeat
        submission changes nothing and sends nothing. Once the window has
        passed (``CONFIRM_WINDOW_HOURS``) a new submission issues a fresh
        token and one new mail — a ceiling of one message per address per
        three days, from any number of submissions, from any number of
        sources. The per-address rate limit above is the second line of the
        same defence, bucketed on the address being targeted rather than on
        the address doing the targeting.

        **An unsubscribed address is not re-subscribed by this call.** The
        status stays ``unsubscribed`` and a confirmation link is sent; only
        clicking it brings them back. A ``complained`` address — someone who
        reported us as spam — is sent nothing at all and stays suppressed:
        mailing them again is how a sender loses its domain reputation.
        [TO CONFIRM with counsel: whether a confirmation mail to a previously
        unsubscribed address is itself acceptable here. The conservative
        alternative is to require the request to come in by hand.]
        """
        data = json_object()
        address = _email_address(data.get("email"))
        consent_text = text(data.get("consent_text"), "consent_text", 400, required=True)
        name = text(data.get("name") or "", "name", 150) or None
        language = (text(data.get("language") or "en", "language", 5) or "en").lower()
        if language not in ("en", "ne"):
            raise BadRequest("language must be 'en' or 'ne'")
        source = text(data.get("source") or "website", "source", 60) or "website"
        country = (text(data.get("country") or "", "country", 2) or "").upper() or None

        evidence = request_fingerprint()
        contact = MarketingContact.query.filter_by(email=address).first()

        if contact is None:
            contact = MarketingContact(
                email=address, name=name, language=language, country=country,
                status=MarketingContact.PENDING, tags=[],
                source=source, consent_text=consent_text,
                signup_ip_hash=evidence["ip_hash"],
            )
            db.session.add(contact)
            db.session.flush()
            send_confirmation_email(contact)
        elif contact.status == MarketingContact.COMPLAINED:
            # A spam complaint is a standing instruction. Not even a
            # confirmation request goes out.
            pass
        elif _confirmation_in_flight(contact):
            # A live link is already in their inbox. Sending a second one adds
            # nothing for them and everything for an abuser.
            pass
        elif contact.status == MarketingContact.SUBSCRIBED:
            # Already on the list. Silence is the correct behaviour: a "you are
            # already subscribed" mail is itself a message they did not ask for
            # on this occasion, and the response below reveals nothing.
            pass
        else:
            # Pending-but-stale, unsubscribed or bounced. The consent evidence
            # is replaced because this is a fresh request, and the status is
            # left where it is: only the confirmation moves it.
            contact.source = source
            contact.consent_text = consent_text
            contact.signup_ip_hash = evidence["ip_hash"]
            if name:
                contact.name = name
            contact.language = language
            if country:
                contact.country = country
            send_confirmation_email(contact)

        db.session.commit()
        return {
            "status": "pending_confirmation",
            "message": ("If that address can be subscribed, a confirmation link "
                        "is on its way. Nothing is sent to you until you use it."),
        }, 202


@marketing_ns.route("/confirm/<string:token>")
@marketing_ns.param("token", "The confirmation token from the email")
class ConfirmSubscription(Resource):
    @marketing_ns.doc("marketing_confirm")
    @marketing_ns.response(200, "Subscription confirmed")
    @marketing_ns.response(404, "Token unknown or already used")
    @marketing_ns.response(410, "Token expired")
    @limiter.limit("20 per hour", key_func=rate_limit_key)
    def get(self, token):
        """Complete the double opt-in.

        GET as well as POST because the link is clicked from a mail client,
        and a form post is not something a mail client does. The cost is that
        a link-scanning security appliance can confirm on the recipient's
        behalf; the alternative — a landing page with a button — needs a
        frontend page that does not exist yet, and is the upgrade path when
        it does.

        One use only: the token is cleared on success, so a second visit gets
        the same 404 as a guessed token. Past ``CONFIRM_WINDOW_HOURS`` the
        link is refused with a message saying so rather than a bare 404,
        because "it expired, ask again" is actionable and "not found" is not.
        """
        return self._confirm(token)

    @marketing_ns.doc("marketing_confirm_post")
    @limiter.limit("20 per hour", key_func=rate_limit_key)
    def post(self, token):
        """Complete the double opt-in. Identical to the GET."""
        return self._confirm(token)

    @staticmethod
    def _confirm(token):
        if not token or len(token) > 64:
            marketing_ns.abort(404, "This confirmation link is not valid.")
        contact = MarketingContact.query.filter_by(confirm_token=token).first()
        if contact is None:
            marketing_ns.abort(
                404, "This confirmation link is not valid, or it has already been used.")
        if not _confirmation_in_flight(contact):
            marketing_ns.abort(
                410,
                f"This confirmation link expired after "
                f"{MarketingContact.CONFIRM_WINDOW_HOURS} hours. "
                "Please subscribe again to get a new one.")

        evidence = request_fingerprint()
        contact.confirm(ip_hash=evidence["ip_hash"])
        # A returning contact: the suppression history stays as evidence of the
        # earlier objection, but it no longer suppresses, because they have
        # just asked for mail from the mailbox itself.
        contact.unsubscribed_at = None
        contact.unsubscribe_reason = None
        db.session.commit()
        return {
            "status": contact.status,
            "email": contact.email,
            "confirmed_at": contact.confirmed_at.isoformat(),
            "message": "Subscription confirmed. You can unsubscribe from any email we send.",
        }, 200


@marketing_ns.route("/unsubscribe/<string:token>")
@marketing_ns.param("token", "The unsubscribe token from the email footer")
class OneClickUnsubscribe(Resource):
    @marketing_ns.doc("marketing_unsubscribe_token")
    @marketing_ns.response(200, "Unsubscribed")
    @marketing_ns.response(404, "Token unknown")
    def get(self, token):
        """Honour a one-click unsubscribe. No login, no confirmation step.

        Art. 7(3) and the ePrivacy rule on direct marketing both come to the
        same place: leaving must be at least as easy as joining, and this is
        one request with no account and no second page. GET is supported
        because that is what a mail client follows, POST because that is what
        RFC 8058's List-Unsubscribe-Post sends.

        Idempotent, and the token keeps working afterwards — a second click
        from the same old email must not produce an error page. The row is not
        deleted: per the model docstring it stays as the suppression record,
        which is the only thing that stops the next import putting them back.
        """
        return self._unsubscribe(token)

    @marketing_ns.doc("marketing_unsubscribe_token_post")
    def post(self, token):
        """Honour a one-click unsubscribe. Identical to the GET."""
        return self._unsubscribe(token)

    @staticmethod
    def _unsubscribe(token):
        if not token or len(token) > 64:
            marketing_ns.abort(404, "This unsubscribe link is not valid.")
        contact = MarketingContact.query.filter_by(unsubscribe_token=token).first()
        if contact is None:
            marketing_ns.abort(
                404,
                "This unsubscribe link is not valid. Write to us and we will "
                "take the address off by hand.")

        already = contact.status == MarketingContact.UNSUBSCRIBED
        if not already:
            contact.unsubscribe(reason="one-click")
            campaign_id = request.args.get("c")
            if campaign_id and campaign_id.isdigit():
                campaign = db.session.get(EmailCampaign, int(campaign_id))
                if campaign is not None:
                    campaign.unsubscribed = (campaign.unsubscribed or 0) + 1
        db.session.commit()
        return {
            "status": contact.status,
            "already_unsubscribed": already,
            "message": "You have been unsubscribed. We will not email you again.",
        }, 200


@marketing_ns.route("/unsubscribe")
class UnsubscribeByEmail(Resource):
    @marketing_ns.doc("marketing_unsubscribe_request")
    @marketing_ns.expect(unsubscribe_input)
    @marketing_ns.response(202, "Accepted")
    @limiter.limit("5 per hour", key_func=rate_limit_key)
    @limiter.limit("3 per day", key_func=_subscribe_email_key)
    def post(self):
        """Email someone their unsubscribe link. Does **not** unsubscribe them.

        Acting on this directly would let anyone remove anyone: the body is
        unauthenticated and the address is public. So the address gets its own
        link and the decision stays with whoever reads that mailbox. The
        tokened route above is the one that acts.

        Only a live subscriber is written to, which keeps this from becoming a
        second way to mail an address repeatedly, and the answer is the same
        either way so it is not a membership oracle.
        """
        data = json_object()
        address = _email_address(data.get("email"))
        contact = MarketingContact.query.filter_by(email=address).first()
        if contact is not None and contact.status == MarketingContact.SUBSCRIBED:
            link = unsubscribe_url(contact)
            _deliver(
                contact.email,
                "Your Moonlight Freight unsubscribe link",
                ("Someone asked us to send the unsubscribe link for this address.\n\n"
                 "Nothing has changed yet. To stop all Moonlight Freight marketing "
                 f"email, use this link:\n\n{link}\n\n"
                 "If you did not ask for this, you can ignore it.\n"),
            )
            db.session.commit()
        return {
            "status": "accepted",
            "message": ("If that address is on our list, the unsubscribe link is "
                        "on its way to it."),
        }, 202


# ── Administration: contacts ─────────────────────────────────────────────────

@marketing_ns.route("/contacts")
class MarketingContacts(Resource):
    @marketing_ns.doc("marketing_list_contacts", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """The contact list, paginated.

        Filters: ``status``, ``tag``, ``country``, ``q`` (email or name).

        ``tag`` is applied in Python rather than SQL. ``tags`` is a JSON
        column and the containment operators differ between SQLite and MySQL;
        a wrong-but-portable ``LIKE`` would match ``diaspora-uk`` for the tag
        ``uk``. At this list size the cost is a full scan of the filtered set,
        which is worth paying for an answer that is correct on both engines.
        """
        page, per_page = pagination()
        query = MarketingContact.query

        status = request.args.get("status")
        if status:
            if status not in MarketingContact.STATUSES:
                raise BadRequest(
                    f"status must be one of {', '.join(MarketingContact.STATUSES)}")
            query = query.filter(MarketingContact.status == status)
        country = request.args.get("country")
        if country:
            query = query.filter(MarketingContact.country == country[:2].upper())
        term = (request.args.get("q") or "").strip()
        if term:
            like = f"%{term[:100]}%"
            query = query.filter(db.or_(MarketingContact.email.ilike(like),
                                        MarketingContact.name.ilike(like)))

        query = query.order_by(MarketingContact.created_at.desc(),
                               MarketingContact.id.desc())
        tag = (request.args.get("tag") or "").strip()
        if tag:
            matched = [c for c in query.all() if tag in (c.tags or [])]
            total = len(matched)
            start = (page - 1) * per_page
            items = matched[start:start + per_page]
            pages = (total + per_page - 1) // per_page
        else:
            page_obj = query.paginate(page=page, per_page=per_page, error_out=False)
            items, total, pages = page_obj.items, page_obj.total, page_obj.pages

        return {
            "items": [c.to_dict() for c in items],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
        }, 200


@marketing_ns.route("/contacts/<int:contact_id>")
@marketing_ns.param("contact_id", "The contact identifier")
@marketing_ns.response(404, "Contact not found")
class MarketingContactDetail(Resource):
    @marketing_ns.doc("marketing_get_contact", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, contact_id):
        """One contact, with the consent evidence attached.

        This is the view to open when somebody asks "why are you emailing
        me?": the wording they were shown, where the form was, when the
        confirmation went out and when it came back, and the hashed addresses
        either end of it.
        """
        return _load_contact(contact_id).to_dict(include_evidence=True), 200

    @marketing_ns.doc("marketing_update_contact", security="Bearer")
    @marketing_ns.expect(contact_update_input)
    @require_roles("admin", "super_admin")
    def put(self, contact_id):
        """Update a contact's tags or language.

        Those two and nothing else. ``email`` is the identity of the consent
        record and changing it would move a confirmed consent onto an address
        that never gave one; ``status`` is named explicitly so an attempt to
        set it gets told no rather than being quietly dropped — promotion to
        ``subscribed`` happens on confirmation and nowhere else.
        """
        contact = _load_contact(contact_id)
        data = json_object()
        for forbidden in ("status", "email", "confirmed_at", "confirm_token",
                          "unsubscribe_token", "consent_text"):
            if forbidden in data:
                marketing_ns.abort(
                    403,
                    f"'{forbidden}' is not editable here. A contact becomes "
                    "subscribed by confirming from their own inbox; use "
                    "/contacts/<id>/suppress to stop mail.")
        if "tags" in data:
            contact.tags = _tags_argument(data["tags"])
        if "language" in data:
            language = (text(data["language"], "language", 5) or "en").lower()
            if language not in ("en", "ne"):
                raise BadRequest("language must be 'en' or 'ne'")
            contact.language = language
        if "name" in data:
            contact.name = text(data["name"] or "", "name", 150) or None
        db.session.commit()
        return contact.to_dict(include_evidence=True), 200


@marketing_ns.route("/contacts/<int:contact_id>/suppress")
@marketing_ns.param("contact_id", "The contact identifier")
@marketing_ns.response(404, "Contact not found")
class MarketingContactSuppress(Resource):
    @marketing_ns.doc("marketing_suppress_contact", security="Bearer")
    @require_roles("admin", "super_admin")
    def post(self, contact_id):
        """Suppress a contact by hand, for an objection that arrived off-channel.

        People unsubscribe by replying, by phone, and by telling someone at a
        counter. Those have to land somewhere durable or they are honoured
        once and forgotten, so this writes the same suppression record the
        one-click link does. ``reason`` is free text and worth filling in.
        """
        contact = _load_contact(contact_id)
        data = json_object()
        reason = text(data.get("reason") or "suppressed by administrator",
                      "reason", 200) or "suppressed by administrator"
        complained = bool(data.get("complained"))
        contact.unsubscribe(reason=reason)
        if complained:
            # A spam complaint is stronger than an unsubscribe: it also bars
            # the confirmation mail that would otherwise let them back in.
            contact.status = MarketingContact.COMPLAINED
        db.session.commit()
        return contact.to_dict(include_evidence=True), 200


@marketing_ns.route("/contacts/import")
class MarketingContactImport(Resource):
    @marketing_ns.doc("marketing_import_contacts", security="Bearer")
    @marketing_ns.expect(import_input)
    @require_roles("admin", "super_admin")
    def post(self):
        """Load addresses as **pending** contacts. They still have to confirm.

        Importing a bought, scraped or inherited list and treating it as
        subscribed is the single most common way a company ends up in breach
        here, and it is the reason this endpoint cannot create a subscribed
        contact: every row lands as ``pending`` and is unmailable until the
        person clicks a link sent to their own mailbox. The claimed origin of
        the list (``source``) and the wording it is claimed to carry
        (``consent_text``) are stored on every row, so that if the claim turns
        out to be false the damage can be scoped to exactly these addresses.

        ``send_confirmation`` defaults to false. Switching it on mails every
        new row once, which is a bulk message to people who have not asked for
        one; do it only for a list where the consent claim is real and recent.
        Addresses already on file are left exactly as they are — an import
        never resurrects a suppressed address, and never resets a confirmation
        already in flight.
        """
        data = json_object()
        rows = data.get("contacts")
        if not isinstance(rows, list) or not rows:
            raise BadRequest("contacts must be a non-empty list")
        if len(rows) > 1000:
            raise BadRequest("Import at most 1000 contacts per call")
        source = text(data.get("source"), "source", 60, required=True)
        consent_text = text(data.get("consent_text"), "consent_text", 400, required=True)
        send_confirmation = bool(data.get("send_confirmation"))

        created, skipped, invalid = [], [], []
        for row in rows:
            if not isinstance(row, dict):
                raise BadRequest("Each contact must be an object")
            try:
                address = _email_address(row.get("email"))
            except BadRequest:
                invalid.append(str(row.get("email"))[:100])
                continue
            if MarketingContact.query.filter_by(email=address).first():
                skipped.append(address)
                continue
            language = (str(row.get("language") or "en")).lower()
            contact = MarketingContact(
                email=address,
                name=text(row.get("name", "") or "", "name", 150) or None,
                language=language if language in ("en", "ne") else "en",
                country=(str(row.get("country") or "")[:2].upper() or None),
                status=MarketingContact.PENDING,
                tags=_tags_argument(row.get("tags", [])),
                source=f"import:{source}"[:60],
                consent_text=consent_text,
            )
            db.session.add(contact)
            db.session.flush()
            if send_confirmation:
                send_confirmation_email(contact)
            created.append(address)

        db.session.commit()
        return {
            "created": len(created),
            "skipped_existing": len(skipped),
            "invalid": invalid[:20],
            "status": MarketingContact.PENDING,
            "confirmation_sent": send_confirmation,
            "note": ("Imported contacts are pending and cannot be mailed a campaign "
                     "until each one confirms from their own inbox."),
        }, 201


def _load_contact(contact_id):
    contact = db.session.get(MarketingContact, contact_id)
    if contact is None:
        marketing_ns.abort(404, "Contact not found")
    return contact


# ── Administration: campaigns ────────────────────────────────────────────────

def _load_campaign(campaign_id):
    campaign = db.session.get(EmailCampaign, campaign_id)
    if campaign is None:
        marketing_ns.abort(404, "Campaign not found")
    return campaign


def _validate_audience(value):
    """Check an audience definition. ``{}`` means confirmed subscribers."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BadRequest("audience must be an object")
    audience = {}
    if "tags" in value:
        audience["tags"] = _tags_argument(value["tags"], "audience.tags")
    if "status" in value:
        statuses = value["status"]
        if isinstance(statuses, str):
            statuses = [statuses]
        if not isinstance(statuses, list) or not statuses:
            raise BadRequest("audience.status must be a status or a list of statuses")
        unknown = [s for s in statuses if s not in MarketingContact.STATUSES]
        if unknown:
            raise BadRequest(
                f"audience.status may only contain {', '.join(MarketingContact.STATUSES)}")
        audience["status"] = statuses
    if "country" in value and value["country"]:
        countries = value["country"]
        if isinstance(countries, str):
            countries = [countries]
        if not isinstance(countries, list):
            raise BadRequest("audience.country must be a country code or a list")
        audience["country"] = [str(c)[:2].upper() for c in countries if c]
    return audience


def audience_contacts(campaign):
    """The contacts a campaign's audience selects, mailable or not.

    Unmailable members are kept in the result deliberately: the send writes a
    skipped row for each one, which is what makes a thin campaign explainable
    afterwards instead of simply small.
    """
    audience = campaign.audience or {}
    statuses = audience.get("status") or list(DEFAULT_AUDIENCE_STATUS)
    query = MarketingContact.query.filter(MarketingContact.status.in_(statuses))
    if audience.get("country"):
        query = query.filter(MarketingContact.country.in_(audience["country"]))
    contacts = query.order_by(MarketingContact.id.asc()).all()
    tags = audience.get("tags")
    if tags:
        wanted = set(tags)
        contacts = [c for c in contacts if wanted & set(c.tags or [])]
    return contacts


def _skip_reason(contact):
    """Why this contact must not be mailed, or ``None`` if they may be."""
    if not contact.email:
        return SKIP_NO_ADDRESS
    if contact.status == MarketingContact.COMPLAINED:
        return SKIP_COMPLAINED
    if contact.status == MarketingContact.UNSUBSCRIBED:
        return SKIP_UNSUBSCRIBED
    if contact.status == MarketingContact.BOUNCED:
        return SKIP_BOUNCED
    if not contact.is_mailable:
        return SKIP_UNCONFIRMED
    return None


@marketing_ns.route("/campaigns")
class EmailCampaigns(Resource):
    @marketing_ns.doc("marketing_list_campaigns", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """Campaigns, paginated. Filters: ``status``, ``q`` (code or name)."""
        page, per_page = pagination()
        query = EmailCampaign.query
        status = request.args.get("status")
        if status:
            if status not in EmailCampaign.STATUSES:
                raise BadRequest(
                    f"status must be one of {', '.join(EmailCampaign.STATUSES)}")
            query = query.filter(EmailCampaign.status == status)
        term = (request.args.get("q") or "").strip()
        if term:
            like = f"%{term[:100]}%"
            query = query.filter(db.or_(EmailCampaign.code.ilike(like),
                                        EmailCampaign.name.ilike(like)))
        page_obj = (query.order_by(EmailCampaign.created_at.desc(), EmailCampaign.id.desc())
                    .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "items": [c.to_dict() for c in page_obj.items],
            "total": page_obj.total,
            "page": page,
            "per_page": per_page,
            "pages": page_obj.pages,
        }, 200

    @marketing_ns.doc("marketing_create_campaign", security="Bearer")
    @marketing_ns.expect(campaign_input)
    @require_roles("admin", "super_admin")
    def post(self):
        """Draft a campaign. Drafting is not approving and not sending."""
        data = json_object()
        code = text(data.get("code"), "code", 60, required=True)
        if EmailCampaign.query.filter_by(code=code).first():
            marketing_ns.abort(409, f"A campaign with code '{code}' already exists")
        campaign = EmailCampaign(
            code=code,
            name=text(data.get("name"), "name", 150, required=True),
            subject=text(data.get("subject"), "subject", 200, required=True),
            preheader=text(data.get("preheader") or "", "preheader", 200) or None,
            body_en=data.get("body_en") or None,
            body_ne=data.get("body_ne") or None,
            audience=_validate_audience(data.get("audience")),
            utm_campaign=text(data.get("utm_campaign") or "", "utm_campaign", 80) or None,
            status=EmailCampaign.DRAFT,
            created_by=_current_user_id(),
        )
        if data.get("ad_campaign_id") is not None:
            if not isinstance(data["ad_campaign_id"], int) or isinstance(data["ad_campaign_id"], bool):
                raise BadRequest("ad_campaign_id must be an integer")
            campaign.ad_campaign_id = data["ad_campaign_id"]
        if data.get("scheduled_for"):
            campaign.scheduled_for = _iso_datetime(data["scheduled_for"], "scheduled_for")
        db.session.add(campaign)
        db.session.commit()
        return campaign.to_dict(), 201


def _iso_datetime(value, field):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise BadRequest(f"{field} must be an ISO 8601 date-time")


@marketing_ns.route("/campaigns/<int:campaign_id>")
@marketing_ns.param("campaign_id", "The campaign identifier")
@marketing_ns.response(404, "Campaign not found")
class EmailCampaignDetail(Resource):
    @marketing_ns.doc("marketing_get_campaign", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, campaign_id):
        """One campaign, with its audience definition."""
        campaign = _load_campaign(campaign_id)
        payload = campaign.to_dict()
        payload["body_en"] = campaign.body_en
        payload["body_ne"] = campaign.body_ne
        payload["audience_size"] = len(audience_contacts(campaign))
        return payload, 200

    @marketing_ns.doc("marketing_update_campaign", security="Bearer")
    @marketing_ns.expect(campaign_input)
    @require_roles("admin", "super_admin")
    def put(self, campaign_id):
        """Edit a campaign that has not gone out.

        **Editing the content withdraws the approval.** Otherwise approval
        means nothing: approve a harmless draft, rewrite the body, send. The
        approver has to see what actually goes out, so any change to subject,
        body, preheader or audience clears ``approved_at`` and the campaign
        has to be approved again.
        """
        campaign = _load_campaign(campaign_id)
        if campaign.status in (EmailCampaign.SENDING, EmailCampaign.SENT):
            marketing_ns.abort(409, f"Campaign {campaign.code} has already been sent")
        data = json_object()
        material = False
        if "name" in data:
            campaign.name = text(data["name"], "name", 150, required=True)
        if "subject" in data:
            campaign.subject = text(data["subject"], "subject", 200, required=True)
            material = True
        if "preheader" in data:
            campaign.preheader = text(data["preheader"] or "", "preheader", 200) or None
            material = True
        for field in ("body_en", "body_ne"):
            if field in data:
                setattr(campaign, field, data[field] or None)
                material = True
        if "audience" in data:
            campaign.audience = _validate_audience(data["audience"])
            material = True
        if "utm_campaign" in data:
            campaign.utm_campaign = text(data["utm_campaign"] or "", "utm_campaign", 80) or None
        if "scheduled_for" in data:
            campaign.scheduled_for = (_iso_datetime(data["scheduled_for"], "scheduled_for")
                                      if data["scheduled_for"] else None)
        if material and campaign.is_approved:
            campaign.approved_at = None
            campaign.approved_by = None
        db.session.commit()
        return campaign.to_dict(), 200

    @marketing_ns.doc("marketing_delete_campaign", security="Bearer")
    @require_roles("admin", "super_admin")
    def delete(self, campaign_id):
        """Delete a draft. A campaign that has sent anything is kept as the record."""
        campaign = _load_campaign(campaign_id)
        if campaign.status != EmailCampaign.DRAFT or campaign.sends:
            marketing_ns.abort(
                409,
                "Only an untouched draft can be deleted; a campaign that has sent "
                "is the evidence of what went to whom.")
        db.session.delete(campaign)
        db.session.commit()
        return {"deleted": campaign_id}, 200


@marketing_ns.route("/campaigns/<int:campaign_id>/approve")
@marketing_ns.param("campaign_id", "The campaign identifier")
@marketing_ns.response(404, "Campaign not found")
class EmailCampaignApprove(Resource):
    @marketing_ns.doc("marketing_approve_campaign", security="Bearer")
    @require_roles("admin", "super_admin")
    def post(self, campaign_id):
        """Approve a campaign for sending. Without this, ``/send`` refuses.

        Mailing the whole list is not a one-click action, and the name against
        ``approved_by`` is the point: somebody read what goes out.

        [TO CONFIRM with the owner: whether the approver must be a different
        person from the creator. Four-eyes is the better control and is not
        enforced here, because a one-administrator office would then be unable
        to send at all.]
        """
        campaign = _load_campaign(campaign_id)
        if campaign.status in (EmailCampaign.SENT, EmailCampaign.CANCELLED):
            marketing_ns.abort(409, f"Campaign {campaign.code} is {campaign.status}")
        if not (campaign.body_en or campaign.body_ne):
            marketing_ns.abort(400, "A campaign with no body cannot be approved")
        campaign.approved_by = _current_user_id()
        campaign.approved_at = datetime.utcnow()
        db.session.commit()
        return campaign.to_dict(), 200


@marketing_ns.route("/campaigns/<int:campaign_id>/test")
@marketing_ns.param("campaign_id", "The campaign identifier")
@marketing_ns.response(404, "Campaign not found")
class EmailCampaignTest(Resource):
    @marketing_ns.doc("marketing_test_campaign", security="Bearer")
    @require_roles("admin", "super_admin")
    def post(self, campaign_id):
        """Send a proof to one named address.

        One address, named in the body, and nothing else: no audience is
        evaluated and no ``EmailSend`` rows are written, so a test can never
        consume the once-per-contact slot that the real send needs. The
        subject is prefixed so a proof is never mistaken for the real thing in
        a shared mailbox.

        If the named address happens to be a contact, its real unsubscribe
        link is used. If not, the footer carries a placeholder — a colleague
        checking a layout is not a subscriber, and minting a token for them
        would create a contact record that nobody consented to.
        """
        campaign = _load_campaign(campaign_id)
        data = json_object()
        address = _email_address(data.get("email"))

        contact = MarketingContact.query.filter_by(email=address).first()
        if contact is not None and contact.unsubscribe_token:
            link = unsubscribe_url(contact)
            preview = contact
        else:
            link = TEST_UNSUBSCRIBE_PLACEHOLDER
            preview = MarketingContact(email=address, name=None, language="en")
        subject, body = render_campaign_email(campaign, preview, link)
        ok, error = _deliver(address, f"[TEST] {subject}", body)
        db.session.commit()
        return {
            "campaign_id": campaign.id,
            "recipient": address,
            "sent": ok,
            "error": error,
        }, 200 if ok else 503


@marketing_ns.route("/campaigns/<int:campaign_id>/send")
@marketing_ns.param("campaign_id", "The campaign identifier")
@marketing_ns.response(404, "Campaign not found")
class EmailCampaignSend(Resource):
    @marketing_ns.doc("marketing_send_campaign", security="Bearer")
    @marketing_ns.expect(send_input)
    @marketing_ns.response(409, "Campaign is not approved")
    @require_roles("admin", "super_admin")
    def post(self, campaign_id):
        """Send the campaign to its audience.

        Three gates, in order: the campaign must be approved, the body must
        carry ``confirm`` set to the campaign's own code (typing the code back
        is what stops the wrong campaign going out from the wrong tab), and
        every recipient must be ``is_mailable`` — confirmed, not suppressed,
        not bounced, not complained. Anyone in the audience who fails that
        last test still gets a row, with ``skip_reason`` saying which test
        they failed.

        **Re-running resumes; it does not resend.** The unique constraint on
        ``(campaign_id, contact_id)`` says a contact can appear once; this
        makes that true in practice by reading the rows that already exist and
        leaving every ``sent`` one alone, and by writing each new row inside a
        savepoint so a concurrent run loses the race rather than duplicating
        the mail. A row previously skipped is reconsidered — a contact who has
        confirmed since the first run is a resume, not a second copy. A row
        that genuinely failed at the MTA is *not* retried automatically,
        because a failure there does not prove non-delivery; the exception is
        a failure recorded as ``not sent - mail not configured``, which
        provably never reached anything.
        """
        campaign = _load_campaign(campaign_id)
        data = json_object()
        if data.get("confirm") != campaign.code:
            marketing_ns.abort(
                400,
                "Set 'confirm' to the campaign code to send. Expected "
                f"'{campaign.code}'.")
        if not campaign.is_approved:
            marketing_ns.abort(
                409,
                f"Campaign {campaign.code} has not been approved. "
                "POST /campaigns/<id>/approve first.")
        if campaign.status == EmailCampaign.CANCELLED:
            marketing_ns.abort(409, f"Campaign {campaign.code} is cancelled")

        campaign.status = EmailCampaign.SENDING
        campaign.started_at = campaign.started_at or datetime.utcnow()
        db.session.commit()

        existing = {row.contact_id: row
                    for row in EmailSend.query.filter_by(campaign_id=campaign.id).all()}
        attempted = failed = skipped = already = 0

        for contact in audience_contacts(campaign):
            row = existing.get(contact.id)
            if row is not None and not _is_retryable(row):
                already += 1
                continue

            reason = _skip_reason(contact)
            if row is None:
                row = EmailSend(campaign_id=campaign.id, contact_id=contact.id,
                                status=EmailSend.QUEUED)
                db.session.add(row)
                try:
                    # A savepoint, so that losing the unique-constraint race to
                    # a concurrent run costs this one contact rather than the
                    # whole send.
                    with db.session.begin_nested():
                        db.session.flush()
                except IntegrityError:
                    db.session.rollback()
                    already += 1
                    continue

            if reason:
                row.status = EmailSend.SKIPPED
                row.skip_reason = reason
                row.error = None
                skipped += 1
                continue

            link = unsubscribe_url(contact, campaign)
            subject, body = render_campaign_email(campaign, contact, link)
            ok, error = _deliver(contact.email, subject, body)
            row.skip_reason = None
            if ok:
                row.status = EmailSend.SENT
                row.error = None
                row.sent_at = datetime.utcnow()
                contact.last_sent_at = row.sent_at
                attempted += 1
            else:
                row.status = EmailSend.FAILED
                row.error = error
                failed += 1

        rows = EmailSend.query.filter_by(campaign_id=campaign.id).all()
        campaign.recipients = sum(1 for r in rows if r.status != EmailSend.SKIPPED)
        campaign.delivered = sum(1 for r in rows if r.status == EmailSend.SENT)
        campaign.failed = sum(1 for r in rows if r.status == EmailSend.FAILED)
        campaign.status = EmailCampaign.SENT
        campaign.completed_at = datetime.utcnow()
        db.session.commit()

        return {
            "campaign_id": campaign.id,
            "sent_now": attempted,
            "failed_now": failed,
            "skipped": skipped,
            "already_handled": already,
            "recipients": campaign.recipients,
            "delivered": campaign.delivered,
            "mail_configured": _mail_is_configured(),
        }, 200


def _is_retryable(row):
    """Whether a send row from an earlier run may be attempted again.

    Only one failure is safe to retry blind: the one where there was no mail
    server to try. Everything else either succeeded or may have succeeded.
    """
    if row.status == EmailSend.SKIPPED:
        return True
    return row.status == EmailSend.FAILED and row.error == MAIL_NOT_CONFIGURED


@marketing_ns.route("/campaigns/<int:campaign_id>/report")
@marketing_ns.param("campaign_id", "The campaign identifier")
@marketing_ns.response(404, "Campaign not found")
class EmailCampaignReport(Resource):
    @marketing_ns.doc("marketing_campaign_report", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, campaign_id):
        """What happened when this campaign went out.

        Recipients, handed to the mail server, failed, skipped broken down by
        reason, and unsubscribes attributed to this mailing.

        **There is no open rate and no click rate, and there will not be
        one.** A tracking pixel and rewritten links are separate processing
        with their own consent and disclosure obligations, and the model
        docstring records the decision not to carry them. What is here is what
        the company did — the message was accepted by the mail server, or it
        was not — rather than what the reader did. ``delivered`` means handed
        over without the MTA refusing it; it is not proof it reached an inbox,
        and nothing short of a bounce or a complaint will tell us that.
        """
        campaign = _load_campaign(campaign_id)
        rows = EmailSend.query.filter_by(campaign_id=campaign.id).all()
        by_reason = {}
        for row in rows:
            if row.status == EmailSend.SKIPPED:
                by_reason[row.skip_reason or "unknown"] = (
                    by_reason.get(row.skip_reason or "unknown", 0) + 1)
        return {
            "campaign": campaign.to_dict(),
            "recipients": sum(1 for r in rows if r.status != EmailSend.SKIPPED),
            "delivered": sum(1 for r in rows if r.status == EmailSend.SENT),
            "failed": sum(1 for r in rows if r.status == EmailSend.FAILED),
            "queued": sum(1 for r in rows if r.status == EmailSend.QUEUED),
            "skipped": sum(1 for r in rows if r.status == EmailSend.SKIPPED),
            "skipped_by_reason": by_reason,
            "unsubscribes": campaign.unsubscribed or 0,
            "open_rate": None,
            "click_rate": None,
            "tracking_note": ("Opens and clicks are deliberately not measured: no "
                              "tracking pixel is embedded and no link is rewritten."),
        }, 200


@marketing_ns.route("/campaigns/<int:campaign_id>/sends")
@marketing_ns.param("campaign_id", "The campaign identifier")
@marketing_ns.response(404, "Campaign not found")
class EmailCampaignSends(Resource):
    @marketing_ns.doc("marketing_campaign_sends", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, campaign_id):
        """The per-contact send rows, paginated. Filter: ``status``."""
        campaign = _load_campaign(campaign_id)
        page, per_page = pagination()
        query = EmailSend.query.filter_by(campaign_id=campaign.id)
        status = request.args.get("status")
        if status:
            if status not in EmailSend.STATUSES:
                raise BadRequest(f"status must be one of {', '.join(EmailSend.STATUSES)}")
            query = query.filter(EmailSend.status == status)
        page_obj = (query.order_by(EmailSend.id.asc())
                    .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "items": [r.to_dict() for r in page_obj.items],
            "total": page_obj.total,
            "page": page,
            "per_page": per_page,
            "pages": page_obj.pages,
        }, 200
