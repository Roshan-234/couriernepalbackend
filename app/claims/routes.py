"""Claims and disputes over a shipment.

Claims used to be handled over the phone, which left no record of what was
promised to whom. A claim is a row with a queue position, a decision history
and a payout figure that reconciles against the ledger.

Two rules carry the money risk and are enforced in one place each:

* a closed claim (settled, rejected, withdrawn) is immutable -- reopening a
  settled payout by PUT is how a dispute queue becomes a leak;
* only an administrator may approve a claim or set the approved amount. The
  customer states what they are claiming; they do not decide what is paid.
"""

from datetime import datetime

from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.auth.utils import require_roles
from app.extensions import db
from app.models.claim import Claim
from app.models.shipment import Shipment
from app.models.user import User
from app.validation import json_object, number, pagination, text

claim_ns = Namespace('claims', description='Claims and disputes')

#: Statuses an administrator may move a live claim into with /status. The
#: decision endpoints (approve, reject, settle) own the rest, because each of
#: those carries a side effect -- an amount, a reason, a payout date.
_MANUAL_STATUSES = (Claim.OPEN, Claim.UNDER_REVIEW, Claim.AWAITING_CUSTOMER)

_PRIORITIES = ('low', 'normal', 'high', 'urgent')


def _current_user():
    return db.session.get(User, int(get_jwt_identity()))


def _load_claim(claim_id):
    claim = db.session.get(Claim, claim_id)
    if not claim:
        claim_ns.abort(404, "Claim not found")
    return claim


def _require_open(claim):
    """A settled, rejected or withdrawn claim does not change again."""
    if claim.is_closed:
        claim_ns.abort(409, f"Claim {claim.claim_no} is closed ({claim.status}) and cannot be changed")
    return claim


def _may_see(claim, user):
    return user.is_admin or claim.raised_by == user.id or claim.assigned_to == user.id


claim_input = claim_ns.model("ClaimInput", {
    "shipment_id": fields.Integer(required=True, description="A shipment you own"),
    "claim_type": fields.String(required=True, enum=list(Claim.TYPES)),
    "description": fields.String(required=True),
    "claimed_amount": fields.Float(description="What you are claiming, not what is paid"),
    "currency": fields.String(default="NPR"),
    "evidence": fields.List(fields.String, description="Uploaded file references"),
})

decision_input = claim_ns.model("ClaimDecisionInput", {
    "approved_amount": fields.Float(description="Admin only"),
    "resolution_note": fields.String(),
    "rejection_reason": fields.String(),
})


# ── Customer side ────────────────────────────────────────────────────────────

@claim_ns.route("/")
class ClaimList(Resource):
    @claim_ns.doc("list_claims_admin", security="Bearer")
    @claim_ns.response(200, "Paginated claims queue")
    @require_roles("admin", "super_admin")
    def get(self):
        """List every claim (admin, paginated).

        Filters: ``status``, ``claim_type``, ``assigned_to``, ``shipment_id``.
        """
        page, per_page = pagination()
        query = Claim.query

        status = request.args.get("status")
        if status:
            if status not in Claim.STATUSES:
                claim_ns.abort(400, f"'status' must be one of {', '.join(Claim.STATUSES)}")
            query = query.filter_by(status=status)

        claim_type = request.args.get("claim_type")
        if claim_type:
            if claim_type not in Claim.TYPES:
                claim_ns.abort(400, f"'claim_type' must be one of {', '.join(Claim.TYPES)}")
            query = query.filter_by(claim_type=claim_type)

        for key in ("assigned_to", "shipment_id"):
            if request.args.get(key):
                try:
                    query = query.filter_by(**{key: int(request.args[key])})
                except ValueError:
                    claim_ns.abort(400, f"'{key}' must be an integer")

        paginated = (query.order_by(Claim.created_at.desc(), Claim.id.desc())
                     .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "claims": [c.to_dict() for c in paginated.items],
            "total": paginated.total,
            "page": paginated.page,
            "pages": paginated.pages,
            "per_page": per_page,
        }

    @claim_ns.doc("raise_claim", security="Bearer")
    @claim_ns.expect(claim_input)
    @claim_ns.response(201, "Claim raised")
    @claim_ns.response(403, "Not your shipment")
    @jwt_required()
    def post(self):
        """Raise a claim against a shipment you own."""
        user = _current_user()
        if not user:
            claim_ns.abort(404, "User not found")
        data = json_object()

        shipment_id = data.get("shipment_id")
        if not isinstance(shipment_id, int) or isinstance(shipment_id, bool):
            claim_ns.abort(400, "'shipment_id' must be an integer")
        shipment = db.session.get(Shipment, shipment_id)
        if not shipment:
            claim_ns.abort(404, "Shipment not found")
        if not user.is_admin and user.id not in (shipment.user_id, shipment.merchant_id):
            claim_ns.abort(403, "You can only raise a claim against your own shipment")

        claim_type = data.get("claim_type", "other")
        if claim_type not in Claim.TYPES:
            claim_ns.abort(400, f"'claim_type' must be one of {', '.join(Claim.TYPES)}")

        claimed = data.get("claimed_amount", 0.0) or 0.0
        number(claimed, "claimed_amount")

        evidence = data.get("evidence", [])
        if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
            claim_ns.abort(400, "'evidence' must be a list of strings")

        claim_no = Claim.generate_number()
        while Claim.query.filter_by(claim_no=claim_no).first():
            claim_no = Claim.generate_number()

        # Whitelisted construction. Note what is absent: status, approved_amount,
        # assigned_to and resolved_by are the operator's to set, never the
        # claimant's, so they are not read from the payload at all.
        claim = Claim(
            claim_no=claim_no,
            shipment_id=shipment.id,
            raised_by=user.id,
            claim_type=claim_type,
            status=Claim.OPEN,
            description=text(data.get("description", ""), "description", 5000),
            claimed_amount=float(claimed),
            approved_amount=0.0,
            currency=text(data.get("currency", "NPR"), "currency", 10),
            evidence=evidence,
        )
        db.session.add(claim)
        db.session.commit()
        return claim.to_dict(), 201


@claim_ns.route("/mine")
class MyClaims(Resource):
    @claim_ns.doc("list_my_claims", security="Bearer")
    @jwt_required()
    def get(self):
        """List the claims you raised (paginated)."""
        uid = int(get_jwt_identity())
        page, per_page = pagination()
        paginated = (Claim.query.filter_by(raised_by=uid)
                     .order_by(Claim.created_at.desc(), Claim.id.desc())
                     .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "claims": [c.to_dict() for c in paginated.items],
            "total": paginated.total,
            "page": paginated.page,
            "pages": paginated.pages,
            "per_page": per_page,
        }


@claim_ns.route("/<int:claim_id>")
@claim_ns.response(404, "Claim not found")
@claim_ns.param("claim_id", "The claim identifier")
class ClaimDetail(Resource):
    @claim_ns.doc("get_claim", security="Bearer")
    @jwt_required()
    def get(self, claim_id):
        """Fetch a claim you raised, were assigned, or administer."""
        user = _current_user()
        claim = _load_claim(claim_id)
        if not _may_see(claim, user):
            claim_ns.abort(403, "Access denied")
        return claim.to_dict()

    @claim_ns.doc("update_claim", security="Bearer")
    @claim_ns.response(409, "Claim is closed")
    @jwt_required()
    def put(self, claim_id):
        """Add detail to an open claim you raised (description, evidence).

        The amount paid is not editable here -- see ``/approve``.
        """
        user = _current_user()
        claim = _load_claim(claim_id)
        if not _may_see(claim, user):
            claim_ns.abort(403, "Access denied")
        _require_open(claim)

        data = json_object()
        if "description" in data:
            claim.description = text(data["description"], "description", 5000)
        if "evidence" in data:
            evidence = data["evidence"]
            if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
                claim_ns.abort(400, "'evidence' must be a list of strings")
            claim.evidence = evidence
        if "claimed_amount" in data:
            claim.claimed_amount = float(number(data["claimed_amount"], "claimed_amount"))
        if "priority" in data:
            if not user.is_admin:
                claim_ns.abort(403, "Only an administrator may set priority")
            if data["priority"] not in _PRIORITIES:
                claim_ns.abort(400, f"'priority' must be one of {', '.join(_PRIORITIES)}")
            claim.priority = data["priority"]
        if "approved_amount" in data or "status" in data:
            # Named explicitly so a client that sends them gets told no,
            # rather than having them silently dropped.
            claim_ns.abort(403, "Use /approve, /reject, /settle or /status to change a claim's outcome")
        db.session.commit()
        return claim.to_dict()


@claim_ns.route("/<int:claim_id>/withdraw")
@claim_ns.response(404, "Claim not found")
@claim_ns.param("claim_id", "The claim identifier")
class ClaimWithdraw(Resource):
    @claim_ns.doc("withdraw_claim", security="Bearer")
    @claim_ns.response(409, "Claim is closed")
    @jwt_required()
    def post(self, claim_id):
        """Withdraw a claim you raised. This closes it."""
        uid = int(get_jwt_identity())
        claim = _load_claim(claim_id)
        if claim.raised_by != uid:
            claim_ns.abort(403, "You can only withdraw your own claim")
        _require_open(claim)
        claim.status = Claim.WITHDRAWN
        claim.resolved_at = datetime.utcnow()
        claim.resolved_by = uid
        db.session.commit()
        return claim.to_dict()


# ── Operator side ────────────────────────────────────────────────────────────

@claim_ns.route("/<int:claim_id>/assign")
@claim_ns.response(404, "Claim not found")
@claim_ns.param("claim_id", "The claim identifier")
class ClaimAssign(Resource):
    @claim_ns.doc("assign_claim", security="Bearer")
    @claim_ns.response(409, "Claim is closed")
    @require_roles("admin", "super_admin")
    def post(self, claim_id):
        """Assign a claim to a handler (admin)."""
        claim = _require_open(_load_claim(claim_id))
        data = json_object()
        assignee_id = data.get("assigned_to")
        if assignee_id is None:
            claim.assigned_to = None
        else:
            if not isinstance(assignee_id, int) or isinstance(assignee_id, bool):
                claim_ns.abort(400, "'assigned_to' must be an integer or null")
            assignee = db.session.get(User, assignee_id)
            if not assignee:
                claim_ns.abort(404, "Assignee not found")
            claim.assigned_to = assignee.id
        if claim.status == Claim.OPEN:
            claim.status = Claim.UNDER_REVIEW
        db.session.commit()
        return claim.to_dict()


@claim_ns.route("/<int:claim_id>/status")
@claim_ns.response(404, "Claim not found")
@claim_ns.param("claim_id", "The claim identifier")
class ClaimStatus(Resource):
    @claim_ns.doc("move_claim_status", security="Bearer")
    @claim_ns.response(409, "Claim is closed")
    @require_roles("admin", "super_admin")
    def post(self, claim_id):
        """Move a live claim between open, under_review and awaiting_customer."""
        claim = _require_open(_load_claim(claim_id))
        data = json_object()
        status = data.get("status")
        if status not in _MANUAL_STATUSES:
            claim_ns.abort(400,
                           f"'status' must be one of {', '.join(_MANUAL_STATUSES)}. "
                           "Use /approve, /reject or /settle to close a claim.")
        claim.status = status
        if "resolution_note" in data:
            claim.resolution_note = text(data["resolution_note"], "resolution_note", 5000)
        db.session.commit()
        return claim.to_dict()


@claim_ns.route("/<int:claim_id>/approve")
@claim_ns.response(404, "Claim not found")
@claim_ns.param("claim_id", "The claim identifier")
class ClaimApprove(Resource):
    @claim_ns.doc("approve_claim", security="Bearer")
    @claim_ns.expect(decision_input)
    @claim_ns.response(403, "Only an administrator may approve a claim")
    @claim_ns.response(409, "Claim is closed")
    @require_roles("admin", "super_admin")
    def post(self, claim_id):
        """Approve a claim and set the amount to be paid (admin only)."""
        claim = _require_open(_load_claim(claim_id))
        data = json_object()
        amount = data.get("approved_amount", claim.claimed_amount)
        number(amount, "approved_amount")
        if amount > (claim.claimed_amount or 0):
            claim_ns.abort(400, "approved_amount cannot exceed the amount claimed")

        claim.status = Claim.APPROVED
        claim.approved_amount = float(amount)
        claim.rejection_reason = None
        claim.resolution_note = text(data.get("resolution_note", ""), "resolution_note", 5000)
        claim.resolved_by = int(get_jwt_identity())
        claim.resolved_at = datetime.utcnow()
        db.session.commit()
        return claim.to_dict()


@claim_ns.route("/<int:claim_id>/reject")
@claim_ns.response(404, "Claim not found")
@claim_ns.param("claim_id", "The claim identifier")
class ClaimReject(Resource):
    @claim_ns.doc("reject_claim", security="Bearer")
    @claim_ns.expect(decision_input)
    @claim_ns.response(409, "Claim is closed")
    @require_roles("admin", "super_admin")
    def post(self, claim_id):
        """Reject a claim with a reason (admin). This closes it."""
        claim = _require_open(_load_claim(claim_id))
        data = json_object()
        reason = text(data.get("rejection_reason", ""), "rejection_reason", 255, required=True)

        claim.status = Claim.REJECTED
        claim.approved_amount = 0.0
        claim.rejection_reason = reason
        claim.resolution_note = text(data.get("resolution_note", ""), "resolution_note", 5000)
        claim.resolved_by = int(get_jwt_identity())
        claim.resolved_at = datetime.utcnow()
        db.session.commit()
        return claim.to_dict()


@claim_ns.route("/<int:claim_id>/settle")
@claim_ns.response(404, "Claim not found")
@claim_ns.param("claim_id", "The claim identifier")
class ClaimSettle(Resource):
    @claim_ns.doc("settle_claim", security="Bearer")
    @claim_ns.response(409, "Claim is closed, or was never approved")
    @require_roles("admin", "super_admin")
    def post(self, claim_id):
        """Record that an approved claim has been paid (admin). This closes it."""
        claim = _require_open(_load_claim(claim_id))
        if claim.status != Claim.APPROVED:
            claim_ns.abort(409, "Only an approved claim can be settled")

        data = json_object()
        claim.status = Claim.SETTLED
        claim.settled_at = datetime.utcnow()
        claim.resolved_by = int(get_jwt_identity())
        claim.resolved_at = claim.resolved_at or datetime.utcnow()
        if "resolution_note" in data:
            claim.resolution_note = text(data["resolution_note"], "resolution_note", 5000)
        db.session.commit()
        return claim.to_dict()
