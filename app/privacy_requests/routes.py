"""Data subject requests: the machinery behind the rights in the policy.

A privacy policy that promises access and erasure without a queue behind it
is a promise the company cannot keep, and missing the one-month deadline in
Art. 12(3) is itself the breach — not a late reply. So the clock starts when
the request arrives, the deadline is a column, and ``GET /overdue`` is the
single number that belongs on the admin dashboard.

Four rules are enforced here rather than left to whoever is on the queue:

* **Nothing leaves before identity is established.** Export and erasure both
  refuse until ``identity_verified_at`` is set. A subject access request is
  the cheapest way to extract someone else's file: you ask for it in their
  name. Verification is the whole control.
* **A reference is not a credential.** ``GET /requests/<reference>`` returns
  the coarse status and nothing else — not the email, not the details, not
  the outcome. Anyone who finds the reference in a screenshot learns only
  that a request exists and roughly where it is.
* **The bundle is never returned from the admin queue.** It is written to a
  file and the path is returned, so a copy of someone's whole record does not
  end up in an access log, a browser cache or a support ticket.
* **Erasure is considered, not blind.** A courier cannot delete the carriage
  record of a parcel it may be sued over, and cannot delete an invoice the
  tax authority can ask for. Those are anonymised; everything with no reason
  to survive is deleted. :class:`DataRequestErase` lists which is which.
"""

import json
import os
import secrets
from datetime import datetime, timedelta

from flask import current_app, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_restx import Namespace, Resource, fields
from werkzeug.exceptions import BadRequest

from app.auth.utils import require_roles
from app.extensions import db, limiter
from app.models.claim import AddressBookEntry
from app.models.consent import ConsentRecord
from app.models.invoice import Invoice
from app.models.marketing import MarketingContact
from app.models.privacy import DEFAULT_RETENTION, DataRequest, RetentionRule
from app.models.shipment import Shipment
from app.models.user import User, UserStatus
from app.models.user_session import UserSession
from app.net import rate_limit_key
from app.validation import json_object, pagination, text
from app.visitor import subject_key

privacy_ns = Namespace("privacy", description="Data subject requests and retention")


#: The two-month extension in Art. 12(3). Granted with a reason, never by
#: default: the extension exists for genuinely complex requests, and using it
#: as the normal path is how a one-month duty quietly becomes three.
EXTENSION_DAYS = 60

#: Where an admin export is written. Overridable so a deployment can put it on
#: an encrypted volume, and so tests do not write real people's files into the
#: repository's storage directory.
DEFAULT_EXPORT_SUBDIR = "privacy_exports"

#: Fields on a shipment that belong to the *recipient* rather than to the
#: person asking. See :class:`MyDataExport` for the reasoning.
REDACTED_RECIPIENT_FIELDS = ("receiver_address", "receiver_phone", "receiver_email")

_ERASED_MARK = "[erased]"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _email_address(value, field="email"):
    value = text(value, field, 200, required=True).lower()
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain or " " in value:
        raise BadRequest(f"{field} must be a valid email address")
    return value


def _required_note(data, field="note", limit=2000):
    note = text(data.get(field, "") or "", field, limit)
    if not note:
        raise BadRequest(f"{field} is required: say what was done and on what evidence")
    return note


def _load_request(request_id):
    record = db.session.get(DataRequest, request_id)
    if record is None:
        privacy_ns.abort(404, "Request not found")
    return record


def _require_verified(record):
    """No data moves until the person has been shown to be who they say.

    Art. 12(6) allows a controller to ask for the information needed to
    confirm identity, and Art. 5(1)(f) requires it: handing a file to the
    wrong person is a breach caused by answering too helpfully.
    """
    if record.identity_verified_at is None:
        privacy_ns.abort(
            409,
            f"Request {record.reference} has no verified identity. "
            "POST /requests/<id>/verify before exporting or erasing anything.")
    return record


def _unique_reference():
    """A reference nobody else holds. Six hex characters collide eventually."""
    for _ in range(10):
        candidate = DataRequest.generate_reference()
        if not DataRequest.query.filter_by(reference=candidate).first():
            return candidate
    raise RuntimeError("Could not allocate a unique data request reference")


def _export_dir():
    configured = current_app.config.get("PRIVACY_EXPORT_DIR")
    if configured:
        return configured
    return os.path.join(current_app.root_path, "..", "storage", DEFAULT_EXPORT_SUBDIR)


#: How long a written bundle stays on disk before `flask privacy-exports-prune`
#: removes it. Short on purpose: the file exists to be handed to one person
#: through whatever channel the identity check established, and after that it
#: is a complete copy of someone's record sitting in a folder.
#: [TO CONFIRM with the owner: long enough for your delivery process.]
EXPORT_RETENTION_DAYS = 30


def ensure_private_dir(directory):
    """Create the export directory so only the service account can read it.

    `os.makedirs` applies the process umask, which on a typical server
    leaves a directory world-readable. A folder of complete customer
    records is the one place on this system where that is unacceptable, so
    the mode is set explicitly — and also on an existing directory, since
    `makedirs` ignores `mode` when the path is already there and the first
    version of this code therefore hardened only brand-new installs.

    POSIX modes are close to meaningless on Windows, where development
    happens; this is for the Linux host that actually serves the site.
    """
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        # Unsupported filesystem. The directory still exists; the caller
        # should not fail a data-subject request over a permission bit.
        current_app.logger.warning(
            "Could not restrict permissions on the privacy export directory "
            "%s; confirm it is not readable by other accounts.", directory)
    return directory


def _redacted_shipment(shipment):
    """One shipment as it appears in the subject's own export."""
    payload = shipment.to_dict()
    for field in REDACTED_RECIPIENT_FIELDS:
        payload[field] = None
    payload["_recipient_contact_withheld"] = True
    return payload


def build_subject_bundle(user=None, email=None, subject_key_value=None):
    """Everything the company holds about one person, as a JSON-ready dict.

    Used by both the self-service export and the admin one, so the two cannot
    drift into answering the same question differently.

    A request can arrive from someone with no account, so ``email`` is the
    handle that always works and ``user`` fills in the account-side records
    when there is one.
    """
    address = (email or (user.email if user else "") or "").lower()
    bundle = {
        "generated_at": datetime.utcnow().isoformat(),
        "subject": {"email": address, "user_id": user.id if user else None},
        "profile": None,
        "shipments": [],
        "addresses": [],
        "invoices": [],
        "consent_records": [],
        "marketing": None,
        "sessions": [],
        "data_requests": [],
        "notes": {
            "recipient_details": (
                "Shipment recipients' address, phone and email are withheld. "
                "They are another person's contact details; Art. 15(4) says a "
                "copy must not adversely affect the rights of others. The "
                "recipient's name and the destination are kept, because "
                "without them the shipment record is unreadable to the person "
                "who booked it."
            ),
            "completeness": (
                "Operational records created about these shipments by staff "
                "and carriers (tracking scans, delivery attempts, proofs of "
                "delivery) are not in this bundle. Ask for them by reference "
                "if you need them. [TO CONFIRM with counsel: whether these "
                "fall inside the Art. 15 copy by default.]"
            ),
        },
    }

    if user is not None:
        bundle["profile"] = user.to_dict()
        bundle["shipments"] = [
            _redacted_shipment(s) for s in
            Shipment.query.filter_by(user_id=user.id).order_by(Shipment.id.asc()).all()
        ]
        bundle["addresses"] = [
            a.to_dict() for a in
            AddressBookEntry.query.filter_by(user_id=user.id)
            .order_by(AddressBookEntry.id.asc()).all()
        ]
        bundle["invoices"] = [
            i.to_dict() for i in
            Invoice.query.filter_by(created_by=user.id).order_by(Invoice.id.asc()).all()
        ]
        bundle["sessions"] = [
            s.to_dict() for s in
            UserSession.query.filter_by(user_id=user.id)
            .order_by(UserSession.id.asc()).all()
        ]

    consent_filters = []
    if user is not None:
        consent_filters.append(ConsentRecord.user_id == user.id)
    if subject_key_value:
        consent_filters.append(ConsentRecord.subject_key == subject_key_value)
    if consent_filters:
        bundle["consent_records"] = [
            r.to_dict(include_evidence=True) for r in
            ConsentRecord.query.filter(db.or_(*consent_filters))
            .order_by(ConsentRecord.created_at.asc()).all()
        ]

    if address:
        contact = MarketingContact.query.filter_by(email=address).first()
        if contact is not None:
            bundle["marketing"] = contact.to_dict(include_evidence=True)
        bundle["data_requests"] = [
            {"reference": r.reference, "kind": r.kind, "status": r.status,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in DataRequest.query.filter_by(email=address)
            .order_by(DataRequest.id.asc()).all()
        ]
    return bundle


def seed_retention_rules():
    """Install :data:`DEFAULT_RETENTION`. Idempotent.

    An existing rule keeps its ``retain_days`` and ``auto_purge``: those are
    the two fields an administrator will have deliberately changed, and a
    re-seed that resets them would silently shorten a period somebody
    lengthened on advice. Only the descriptive fields are refreshed.
    """
    created = updated = 0
    for code, label, target, retain_days, basis in DEFAULT_RETENTION:
        rule = RetentionRule.query.filter_by(code=code).first()
        if rule is None:
            db.session.add(RetentionRule(
                code=code, label=label, target=target,
                retain_days=retain_days, basis=basis,
                auto_purge=False, is_active=True,
            ))
            created += 1
        else:
            rule.label, rule.target, rule.basis = label, target, basis
            updated += 1
    db.session.commit()
    return {"created": created, "updated": updated, "total": len(DEFAULT_RETENTION)}


# ── Swagger models ───────────────────────────────────────────────────────────

request_input = privacy_ns.model("DataRequestInput", {
    "kind": fields.String(required=True, enum=list(DataRequest.KINDS)),
    "email": fields.String(required=True),
    "details": fields.String(description="What you are asking for, in your own words"),
})

note_input = privacy_ns.model("DataRequestNoteInput", {
    "note": fields.String(required=True),
})

extend_input = privacy_ns.model("DataRequestExtendInput", {
    "reason": fields.String(required=True, description="Why one month is not enough"),
})

erase_input = privacy_ns.model("DataRequestEraseInput", {
    "confirm": fields.String(required=True, description="The request's own reference"),
    "note": fields.String(required=True),
})

retention_update_input = privacy_ns.model("RetentionRuleUpdate", {
    "retain_days": fields.Integer(),
    "basis": fields.String(),
    "auto_purge": fields.Boolean(),
    "is_active": fields.Boolean(),
})


# ── Public ───────────────────────────────────────────────────────────────────

@privacy_ns.route("/requests")
class DataRequests(Resource):
    @privacy_ns.doc("privacy_file_request")
    @privacy_ns.expect(request_input)
    @privacy_ns.response(201, "Filed; the reference is returned")
    @limiter.limit("5 per hour", key_func=rate_limit_key)
    def post(self):
        """File a data subject request. No account needed.

        Rights belong to people, not to account holders: someone who was
        shipped a parcel, or who filled in a contact form, has the same Art.
        15 right as a registered customer and no way to log in. So this is
        open, and identity is established later by a person rather than by a
        password.

        The clock starts here — ``start_clock()`` sets the one-month deadline
        from the moment the request arrives, not from the moment someone
        picks it up.

        Only the reference comes back. Echoing the stored request would make
        this endpoint a way to confirm that an address is on file.
        """
        data = json_object()
        kind = data.get("kind")
        if kind not in DataRequest.KINDS:
            raise BadRequest(f"kind must be one of {', '.join(DataRequest.KINDS)}")
        address = _email_address(data.get("email"))
        details = text(data.get("details", "") or "", "details", 5000) or None

        record = DataRequest(
            reference=_unique_reference(),
            kind=kind,
            status=DataRequest.RECEIVED,
            email=address,
            details=details,
            subject_key=subject_key(),
        )
        matched = User.query.filter_by(email=address).first()
        if matched is not None:
            # A match is a convenience for whoever handles the queue. It is
            # not verification: anyone can type a customer's address.
            record.user_id = matched.id
        record.start_clock()
        db.session.add(record)
        db.session.commit()
        return {
            "reference": record.reference,
            "message": ("Your request has been logged. We will respond within one "
                        "month. Keep this reference; we may ask you to confirm your "
                        "identity before we can act."),
        }, 201


@privacy_ns.route("/requests/<string:reference>")
@privacy_ns.param("reference", "The reference given when the request was filed")
class DataRequestStatus(Resource):
    @privacy_ns.doc("privacy_request_status")
    @privacy_ns.response(404, "No request with that reference")
    @limiter.limit("30 per hour", key_func=rate_limit_key)
    def get(self, reference):
        """The coarse status of a request, and nothing else.

        Deliberately thin. A reference string is not a credential: it travels
        by email, gets pasted into tickets and screenshots, and is readable
        by anyone standing behind the person. So it buys the one fact the
        person needs — where their request has got to — and nothing that
        would be worth stealing it for. No email, no details, no outcome
        note, no handler, not even the deadline, since a deadline plus a
        reference is enough to reconstruct when it was filed.
        """
        record = DataRequest.query.filter_by(reference=(reference or "")[:40]).first()
        if record is None:
            privacy_ns.abort(404, "No request with that reference.")
        return {"reference": record.reference, "status": record.status}, 200


# ── Self-service ─────────────────────────────────────────────────────────────

@privacy_ns.route("/me/export")
class MyDataExport(Resource):
    @privacy_ns.doc("privacy_my_export", security="Bearer")
    @jwt_required()
    @limiter.limit("3 per day", key_func=rate_limit_key)
    def get(self):
        """Your own data, as a JSON bundle. The Art. 15 / Art. 20 answer.

        For the common case — a customer with an account who wants their file
        — a form and a month-long queue are theatre: they are already
        authenticated, so the answer can be immediate and the formal process
        kept for the cases that actually need a human. Portability (Art. 20)
        wants a structured, machine-readable format, which is what this is.

        **On a shipment's recipient.** Those details are two things at once:
        part of the booking the subject made, and another living person's
        name, address, phone and email. Withholding the lot would leave the
        subject unable to recognise their own shipments; handing it over in
        full would let anyone with a customer account harvest the contact
        details of everyone they ever shipped to, which is what Art. 15(4)
        guards against. The split taken here is that the recipient's *name*
        and the *destination* stay, because the subject supplied them and
        cannot read the record without them, and the recipient's address,
        phone and email are withheld. A subject who needs those should ask
        through ``POST /requests``, where a person weighs it.

        Rate limited to three a day: it walks every shipment, invoice and
        session the account owns.
        """
        user = db.session.get(User, int(get_jwt_identity()))
        if user is None:
            privacy_ns.abort(404, "User not found")
        bundle = build_subject_bundle(user=user, email=user.email,
                                      subject_key_value=subject_key())
        return bundle, 200


# ── Administration: the queue ────────────────────────────────────────────────

@privacy_ns.route("/queue")
class DataRequestQueue(Resource):
    @privacy_ns.doc("privacy_queue", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """The request queue, nearest deadline first.

        Filters: ``kind``, ``status``, ``overdue`` (``true``/``false``).

        Sorted by deadline rather than by arrival because the deadline is
        what is actually owed: an extended request filed first is not more
        urgent than an ordinary one filed later. Closed requests sort last —
        they have no deadline left to miss.
        """
        page, per_page = pagination()
        query = DataRequest.query

        kind = request.args.get("kind")
        if kind:
            if kind not in DataRequest.KINDS:
                raise BadRequest(f"kind must be one of {', '.join(DataRequest.KINDS)}")
            query = query.filter(DataRequest.kind == kind)
        status = request.args.get("status")
        if status:
            if status not in DataRequest.STATUSES:
                raise BadRequest(f"status must be one of {', '.join(DataRequest.STATUSES)}")
            query = query.filter(DataRequest.status == status)

        overdue = (request.args.get("overdue") or "").lower()
        if overdue in ("true", "1", "yes"):
            query = _overdue_filter(query)
        elif overdue in ("false", "0", "no"):
            query = query.filter(db.not_(_overdue_condition()))

        deadline = db.func.coalesce(DataRequest.extended_until, DataRequest.due_at)
        closed = DataRequest.status.in_((DataRequest.COMPLETED, DataRequest.REFUSED))
        page_obj = (query.order_by(closed.asc(), deadline.asc(), DataRequest.id.asc())
                    .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "items": [r.to_dict() for r in page_obj.items],
            "total": page_obj.total,
            "page": page,
            "per_page": per_page,
            "pages": page_obj.pages,
        }, 200


def _overdue_condition():
    """SQL for :attr:`DataRequest.is_overdue`, so the queue can filter on it."""
    deadline = db.func.coalesce(DataRequest.extended_until, DataRequest.due_at)
    return db.and_(
        deadline.isnot(None),
        deadline < datetime.utcnow(),
        db.not_(DataRequest.status.in_((DataRequest.COMPLETED, DataRequest.REFUSED))),
    )


def _overdue_filter(query):
    return query.filter(_overdue_condition())


@privacy_ns.route("/overdue")
class DataRequestOverdue(Resource):
    @privacy_ns.doc("privacy_overdue", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """How many requests are past their deadline.

        This is the number for the dashboard. Being late is not a service
        failure that can be made up later — Art. 12(3) sets one month and
        missing it is the infringement, whatever the eventual answer. A
        figure above zero needs someone's morning, not a backlog report.
        """
        query = _overdue_filter(DataRequest.query)
        records = query.order_by(
            db.func.coalesce(DataRequest.extended_until, DataRequest.due_at).asc()).all()
        return {
            "overdue": len(records),
            "due_within_7_days": DataRequest.query.filter(
                db.not_(DataRequest.status.in_((DataRequest.COMPLETED, DataRequest.REFUSED))),
                db.func.coalesce(DataRequest.extended_until, DataRequest.due_at).isnot(None),
                db.func.coalesce(DataRequest.extended_until, DataRequest.due_at)
                >= datetime.utcnow(),
                db.func.coalesce(DataRequest.extended_until, DataRequest.due_at)
                < datetime.utcnow() + timedelta(days=7),
            ).count(),
            "oldest_reference": records[0].reference if records else None,
        }, 200


@privacy_ns.route("/requests/<int:request_id>/verify")
@privacy_ns.param("request_id", "The request identifier")
@privacy_ns.response(404, "Request not found")
class DataRequestVerify(Resource):
    @privacy_ns.doc("privacy_verify_request", security="Bearer")
    @privacy_ns.expect(note_input)
    @require_roles("admin", "super_admin")
    def post(self, request_id):
        """Record that the requester's identity has been established.

        The note is required and should say *how*: which document was seen,
        which account the request was made from, which number was called
        back. "Verified" on its own is not evidence, and this is the field a
        regulator reads when asking how the company knew who it was talking
        to.

        Nothing is exported or erased before this: both of those endpoints
        refuse while ``identity_verified_at`` is null.
        """
        record = _load_request(request_id)
        data = json_object()
        note = _required_note(data, "note", 255)
        record.identity_verified_at = datetime.utcnow()
        record.verification_note = note
        record.handled_by = int(get_jwt_identity())
        if record.status in (DataRequest.RECEIVED, DataRequest.VERIFYING):
            record.status = DataRequest.IN_PROGRESS
        db.session.commit()
        return record.to_dict(), 200


@privacy_ns.route("/requests/<int:request_id>/extend")
@privacy_ns.param("request_id", "The request identifier")
@privacy_ns.response(404, "Request not found")
class DataRequestExtend(Resource):
    @privacy_ns.doc("privacy_extend_request", security="Bearer")
    @privacy_ns.expect(extend_input)
    @require_roles("admin", "super_admin")
    def post(self, request_id):
        """Take the two-month extension. Requires a reason.

        Art. 12(3) allows two further months "where necessary, taking into
        account the complexity and number of the requests", and requires the
        subject to be told within the first month, with the reasons. The
        reason is mandatory here because an extension without one is just a
        missed deadline with better paperwork — and because the text has to
        be sent to the person anyway.
        """
        record = _load_request(request_id)
        if record.status in (DataRequest.COMPLETED, DataRequest.REFUSED):
            privacy_ns.abort(409, f"Request {record.reference} is already {record.status}")
        if record.extended_until is not None:
            privacy_ns.abort(409, "This request has already been extended once")
        data = json_object()
        reason = text(data.get("reason", "") or "", "reason", 255)
        if not reason:
            raise BadRequest("reason is required, and has to be told to the requester")
        base = record.due_at or datetime.utcnow()
        record.extended_until = base + timedelta(days=EXTENSION_DAYS)
        record.extension_reason = reason
        db.session.commit()
        return record.to_dict(), 200


@privacy_ns.route("/requests/<int:request_id>/complete")
@privacy_ns.param("request_id", "The request identifier")
@privacy_ns.response(404, "Request not found")
class DataRequestComplete(Resource):
    @privacy_ns.doc("privacy_complete_request", security="Bearer")
    @privacy_ns.expect(note_input)
    @require_roles("admin", "super_admin")
    def post(self, request_id):
        """Close a request as answered. Requires a note saying what was done."""
        record = _load_request(request_id)
        if record.status in (DataRequest.COMPLETED, DataRequest.REFUSED):
            privacy_ns.abort(409, f"Request {record.reference} is already {record.status}")
        data = json_object()
        record.outcome_note = _required_note(data)
        record.status = DataRequest.COMPLETED
        record.completed_at = datetime.utcnow()
        record.handled_by = int(get_jwt_identity())
        db.session.commit()
        return record.to_dict(), 200


@privacy_ns.route("/requests/<int:request_id>/refuse")
@privacy_ns.param("request_id", "The request identifier")
@privacy_ns.response(404, "Request not found")
class DataRequestRefuse(Resource):
    @privacy_ns.doc("privacy_refuse_request", security="Bearer")
    @privacy_ns.expect(note_input)
    @require_roles("admin", "super_admin")
    def post(self, request_id):
        """Refuse a request. Requires a note giving the grounds.

        Art. 12(4): a refusal has to be explained, and the person told they
        may complain to a supervisory authority and go to court. The note is
        what that letter is written from, so "no" on its own is not accepted
        here.
        """
        record = _load_request(request_id)
        if record.status in (DataRequest.COMPLETED, DataRequest.REFUSED):
            privacy_ns.abort(409, f"Request {record.reference} is already {record.status}")
        data = json_object()
        record.outcome_note = _required_note(data)
        record.status = DataRequest.REFUSED
        record.completed_at = datetime.utcnow()
        record.handled_by = int(get_jwt_identity())
        db.session.commit()
        return record.to_dict(), 200


@privacy_ns.route("/requests/<int:request_id>/export")
@privacy_ns.param("request_id", "The request identifier")
@privacy_ns.response(404, "Request not found")
@privacy_ns.response(409, "Identity not verified")
class DataRequestExport(Resource):
    @privacy_ns.doc("privacy_export_request", security="Bearer")
    @require_roles("admin", "super_admin")
    def post(self, request_id):
        """Write the subject's bundle to a file and return the path.

        **The bundle is not returned inline.** A complete copy of one
        person's record is the most sensitive object this system can produce,
        and an API response is the worst place to put it: it lands in access
        logs, proxy buffers, browser history and whatever the admin UI caches.
        The file goes to ``PRIVACY_EXPORT_DIR`` (``storage/privacy_exports``
        unless configured) and is delivered by whatever channel the identity
        check established.

        Refuses until identity is verified.

        The directory and the file are created 0700/0600 so that only the
        service account can read them, and `flask privacy-exports-prune`
        deletes bundles older than ``EXPORT_RETENTION_DAYS``. Neither is
        encryption: an unattended folder of complete customer files is a
        worse exposure than the records it came from, so the folder should
        be short-lived rather than merely tidy.

        [TO CONFIRM with the owner: whether the host encrypts this volume at
        rest, and whether the prune should run on a schedule.]
        """
        record = _require_verified(_load_request(request_id))
        user = db.session.get(User, record.user_id) if record.user_id else None
        if user is None:
            user = User.query.filter_by(email=record.email).first()
        bundle = build_subject_bundle(user=user, email=record.email,
                                      subject_key_value=record.subject_key)
        bundle["request"] = {
            "reference": record.reference,
            "kind": record.kind,
            "verified_at": record.identity_verified_at.isoformat(),
        }

        directory = ensure_private_dir(_export_dir())
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        filename = f"{record.reference}-{stamp}.json"
        path = os.path.abspath(os.path.join(directory, filename))
        # Opened at 0600 rather than chmod-ed afterwards, so there is no
        # window in which the file exists with the umask's permissions.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(bundle, handle, indent=2, ensure_ascii=False, default=str)

        record.export_path = path[:500]
        record.handled_by = int(get_jwt_identity())
        if record.status == DataRequest.RECEIVED:
            record.status = DataRequest.IN_PROGRESS
        db.session.commit()
        return {
            "reference": record.reference,
            "export_path": record.export_path,
            "sections": sorted(k for k in bundle if not k.startswith("_")),
            "note": "The bundle is on disk. It is deliberately not returned here.",
        }, 201


@privacy_ns.route("/requests/<int:request_id>/erase")
@privacy_ns.param("request_id", "The request identifier")
@privacy_ns.response(404, "Request not found")
@privacy_ns.response(409, "Identity not verified")
class DataRequestErase(Resource):
    @privacy_ns.doc("privacy_erase_request", security="Bearer")
    @privacy_ns.expect(erase_input)
    @require_roles("super_admin")
    def post(self, request_id):
        """Erase the subject. Super-admin only, verified identity, explicit confirm.

        Art. 17 is not a delete button. The right to erasure yields where
        processing is still necessary for compliance with a legal obligation
        or for legal claims (Art. 17(3)(b) and (e)), and a freight company has
        both: the tax authority can ask for an invoice, and a consignee can
        sue over a parcel years after it moved. So the rule here is
        **anonymise what has to survive, delete what does not**, and say which
        is which:

        *Anonymised, kept:*

        - **The account.** Email, username and name are replaced with
          ``erased-<id>`` markers and the login is disabled. The row stays
          because invoices, shipments and the audit log point at it; deleting
          it would either cascade through the ledger or leave it dangling.
        - **Shipments.** The sender-side fields the subject supplied are
          scrubbed. The shipment itself, its route, weight and dates stay:
          that is the carriage record. The *recipient's* details are not
          touched, because they are a third party's data, and this person's
          erasure right does not reach them.
        - **Invoices.** Kept in full and *not* scrubbed. An invoice is the
          document the tax authority asks for, and an anonymous invoice is
          not one. [TO CONFIRM with the owner's accountant: Nepali tax law,
          not GDPR, sets this floor. The retention rule for invoices should
          be written down before the first real erasure.]
        - **Consent records.** Unlinked from the account so they stop being
          personal data tied to a name, but kept as the evidence that consent
          was given and withdrawn.
        - **The marketing contact.** Kept as a suppression record with the
          name, tags and evidence stripped, exactly as the marketing model's
          docstring requires. The address itself has to remain, because an
          address deleted from the suppression list is an address the next
          import mails again — which is the harm the person asked to stop.
          [TO CONFIRM with counsel: retaining the address against the
          person's objection relies on Art. 17(3)(b) and recital 65.]

        *Deleted outright:*

        - **Sessions.** Pure operational state with no reason to survive.
        - **Saved addresses.** A convenience the person entered for
          themselves; nothing depends on it.

        Three gates, because this is the one irreversible endpoint in the
        module: the ``super_admin`` role, a verified identity, and ``confirm``
        set to the request's own reference.
        """
        record = _require_verified(_load_request(request_id))
        data = json_object()
        if data.get("confirm") != record.reference:
            privacy_ns.abort(
                400,
                "Set 'confirm' to the request reference to erase. Expected "
                f"'{record.reference}'. This cannot be undone.")
        note = _required_note(data)

        report = erase_subject(record.email, user_id=record.user_id)
        record.status = DataRequest.COMPLETED
        record.completed_at = datetime.utcnow()
        record.handled_by = int(get_jwt_identity())
        record.outcome_note = f"{note}\n\nErasure report: {json.dumps(report)}"[:65535]
        db.session.commit()
        return {"reference": record.reference, "report": report}, 200


def erase_subject(email, user_id=None):
    """Carry out the erasure described in :class:`DataRequestErase`.

    Returns the report of what was anonymised, deleted and deliberately kept.
    Separated from the route so the decision table can be read, tested and
    argued about on its own.
    """
    address = (email or "").lower()
    user = db.session.get(User, user_id) if user_id else None
    if user is None and address:
        user = User.query.filter_by(email=address).first()

    report = {
        "subject_email": address,
        "user_id": user.id if user else None,
        "anonymised": {},
        "deleted": {},
        "retained": {},
        "notes": [],
    }

    if user is not None:
        sessions = UserSession.query.filter_by(user_id=user.id).all()
        for session in sessions:
            db.session.delete(session)
        report["deleted"]["user_sessions"] = len(sessions)

        entries = AddressBookEntry.query.filter_by(user_id=user.id).all()
        for entry in entries:
            db.session.delete(entry)
        report["deleted"]["address_book_entries"] = len(entries)

        shipments = Shipment.query.filter_by(user_id=user.id).all()
        for shipment in shipments:
            shipment.sender_name = _ERASED_MARK
            shipment.sender_address = _ERASED_MARK
            shipment.sender_phone = None
            shipment.sender_email = None
        report["anonymised"]["shipments"] = len(shipments)
        report["notes"].append(
            "Shipment recipients were left untouched: a third party's details "
            "are not the subject's to erase.")

        consents = ConsentRecord.query.filter_by(user_id=user.id).all()
        for consent in consents:
            consent.user_id = None
        report["anonymised"]["consent_records"] = len(consents)

        invoices = Invoice.query.filter_by(created_by=user.id).count()
        report["retained"]["invoices"] = invoices
        report["notes"].append(
            "Invoices kept in full under Art. 17(3)(b): a tax record that has "
            "been anonymised is not a tax record. [TO CONFIRM with the "
            "owner's accountant.]")

        # The account row is the last thing touched, so that the lookups above
        # still find it.
        user.email = f"erased-{user.id}@erased.invalid"
        user.username = f"erased-{user.id}"
        user.first_name = "Erased"
        user.last_name = "Subject"
        user.status = UserStatus.INACTIVE
        # A real hash of a value nobody holds, rather than a junk string: a
        # malformed hash would make the password check raise rather than
        # simply fail.
        user.set_password(secrets.token_urlsafe(32))
        user.must_change_password = False
        user.password_reset_token = None
        user.password_reset_expires = None
        report["anonymised"]["user"] = 1
    else:
        report["notes"].append("No account matched this address; nothing to anonymise there.")

    if address:
        contact = MarketingContact.query.filter_by(email=address).first()
        if contact is not None:
            contact.unsubscribe(reason="erasure request")
            contact.name = None
            contact.tags = []
            contact.user_id = None
            contact.country = None
            contact.consent_text = None
            contact.confirm_token = None
            contact.confirm_ip_hash = None
            contact.signup_ip_hash = None
            report["retained"]["marketing_suppression"] = 1
            report["notes"].append(
                "The marketing contact is kept as a stripped suppression record: "
                "deleting the address is how the next import mails them again.")

        others = DataRequest.query.filter_by(email=address).count()
        report["retained"]["data_requests"] = others
        report["notes"].append(
            "Data request records are kept as the evidence that the request was "
            "answered in time.")

    db.session.flush()
    return report


# ── Administration: retention ────────────────────────────────────────────────

@privacy_ns.route("/retention")
class RetentionRules(Resource):
    @privacy_ns.doc("privacy_list_retention", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """The retention schedule: what is kept, for how long, and why.

        The ``basis`` column is the one that matters in an audit. Several of
        the seeded periods are marked ``[TO CONFIRM with counsel]`` because
        they are legal judgements rather than engineering ones; those markers
        are meant to be read and removed by a person, not inherited.
        """
        rules = RetentionRule.query.order_by(RetentionRule.code.asc()).all()
        return {
            "items": [r.to_dict() for r in rules],
            "total": len(rules),
            "unconfirmed": sum(1 for r in rules if "[TO CONFIRM" in (r.basis or "")),
        }, 200


@privacy_ns.route("/retention/<int:rule_id>")
@privacy_ns.param("rule_id", "The retention rule identifier")
@privacy_ns.response(404, "Rule not found")
class RetentionRuleDetail(Resource):
    @privacy_ns.doc("privacy_update_retention", security="Bearer")
    @privacy_ns.expect(retention_update_input)
    @require_roles("admin", "super_admin")
    def put(self, rule_id):
        """Change a retention period, its basis, or whether it purges itself.

        ``code`` and ``target`` are fixed: they name the rows the purge job
        will delete, and letting them be edited turns a typo into a deletion
        of the wrong table. ``auto_purge`` stays off until somebody turns it
        on deliberately — the model says so and this keeps it true.
        """
        rule = db.session.get(RetentionRule, rule_id)
        if rule is None:
            privacy_ns.abort(404, "Rule not found")
        data = json_object()
        for forbidden in ("code", "target"):
            if forbidden in data:
                privacy_ns.abort(
                    403, f"'{forbidden}' identifies the rows this rule deletes "
                         "and is not editable")
        if "retain_days" in data:
            days = data["retain_days"]
            if not isinstance(days, int) or isinstance(days, bool) or days < 1:
                raise BadRequest("retain_days must be a positive integer")
            if days > 36500:
                raise BadRequest("retain_days must be under a century")
            rule.retain_days = days
        if "basis" in data:
            rule.basis = text(data["basis"] or "", "basis", 5000) or None
        if "auto_purge" in data:
            if not isinstance(data["auto_purge"], bool):
                raise BadRequest("auto_purge must be true or false")
            rule.auto_purge = data["auto_purge"]
        if "is_active" in data:
            if not isinstance(data["is_active"], bool):
                raise BadRequest("is_active must be true or false")
            rule.is_active = data["is_active"]
        db.session.commit()
        return rule.to_dict(), 200


@privacy_ns.route("/retention/seed")
class RetentionSeed(Resource):
    @privacy_ns.doc("privacy_seed_retention", security="Bearer")
    @require_roles("admin", "super_admin")
    def post(self):
        """Install the default retention schedule. Safe to run again.

        The same work as ``flask seed-privacy``, exposed over the API so a
        deployment without shell access can still get a schedule in place.
        An existing rule keeps the period and the purge flag somebody set;
        only the label, target and basis are refreshed.
        """
        return seed_retention_rules(), 200
