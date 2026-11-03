"""Cash on delivery collection and remittance.

Cash on delivery is the default in Nepal, so the courier spends most of the
day holding money that belongs to its merchants. These endpoints track that
money from the doorstep, through the branch deposit, to the merchant payout,
so the cash in hand can always be reconciled against the ledger.

Endpoints:
  GET  /api/cod/collections                     List (admin: all, merchant: own)
  POST /api/cod/collections/<shipment_id>/collect   Rider records money taken
  POST /api/cod/collections/<id>/deposit        Branch confirms cash received
  POST /api/cod/collections/<id>/verify         Admin verification
  GET  /api/cod/summary                         Per-merchant outstanding totals
  GET  /api/cod/remittances                     List (admin: all, merchant: own)
  POST /api/cod/remittances                     Build one for a merchant/period
  GET  /api/cod/remittances/<id>                Detail, with collections
  POST /api/cod/remittances/<id>/approve        super_admin
  POST /api/cod/remittances/<id>/pay            super_admin, marks collections remitted

Every money figure is derived server-side. A client-supplied total, fee or
net payable is ignored: ``CodRemittance.recalculate()`` is the only thing that
sets the payout.
"""
from datetime import datetime

from flask import request, current_app
from flask_restx import Namespace, Resource
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.exceptions import BadRequest

from app.extensions import db
from app.auth.utils import require_roles
from app.validation import json_object, number, pagination, text
from app.models.cod import CodCollection, CodRemittance
from app.models.shipment import Shipment
from app.models.user import User

cod_ns = Namespace('cod', description='Cash on delivery collection and remittance')

#: Statuses whose money is in the courier's hands but not yet paid out.
OUTSTANDING_STATUSES = (CodCollection.COLLECTED, CodCollection.DEPOSITED)


# ── helpers ──────────────────────────────────────────────────────────────────

def _current_user():
    user = db.session.get(User, int(get_jwt_identity()))
    if not user:
        cod_ns.abort(404, "User not found")
    return user


def _is_admin(user):
    return any(r.name in ("super_admin", "admin") for r in user.roles)


def _parse_date(value, name):
    """Accept an ISO date or datetime and return a ``date``."""
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        raise BadRequest(f"{name} must be an ISO date, e.g. 2025-01-31")


def _amount_matches(a, b):
    """Money comparison with a paisa of slack for float round-trips."""
    return abs(round(float(a or 0.0), 2) - round(float(b or 0.0), 2)) < 0.01


def _optional_int(data, key):
    value = data.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise BadRequest(f"{key} must be an integer")
    return value


def _status(data, allowed, key="status"):
    value = data.get(key)
    if value is not None and value not in allowed:
        raise BadRequest(f"{key} must be one of: {', '.join(allowed)}")
    return value


def _paged(query, order_column, page, per_page):
    pag = query.order_by(order_column.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    return pag


# ── collections ──────────────────────────────────────────────────────────────

@cod_ns.route("/collections")
class CodCollectionList(Resource):
    @cod_ns.doc("list_cod_collections", security="Bearer",
                params={
                    "status": "Filter by collection status",
                    "merchant_id": "Admin only; a merchant always sees their own",
                    "branch_id": "Filter by branch",
                    "rider_id": "Filter by rider",
                    "date_from": "ISO date, inclusive, against created_at",
                    "date_to": "ISO date, inclusive, against created_at",
                })
    @cod_ns.response(200, "Paginated collections")
    @jwt_required()
    def get(self):
        """List COD collections. Admins see every merchant; a merchant sees only their own."""
        user = _current_user()
        page, per_page = pagination()

        query = CodCollection.query
        if _is_admin(user):
            merchant_id = request.args.get("merchant_id", type=int)
            if merchant_id:
                query = query.filter(CodCollection.merchant_id == merchant_id)
        else:
            # A merchant's own money only. Never widen this from a query param.
            query = query.filter(CodCollection.merchant_id == user.id)

        status = request.args.get("status")
        if status:
            if status not in CodCollection.STATUSES:
                raise BadRequest(f"status must be one of: {', '.join(CodCollection.STATUSES)}")
            query = query.filter(CodCollection.status == status)

        branch_id = request.args.get("branch_id", type=int)
        if branch_id:
            query = query.filter(CodCollection.branch_id == branch_id)
        rider_id = request.args.get("rider_id", type=int)
        if rider_id:
            query = query.filter(CodCollection.rider_id == rider_id)

        if request.args.get("date_from"):
            start = _parse_date(request.args["date_from"], "date_from")
            query = query.filter(CodCollection.created_at >= datetime.combine(
                start, datetime.min.time()))
        if request.args.get("date_to"):
            end = _parse_date(request.args["date_to"], "date_to")
            query = query.filter(CodCollection.created_at <= datetime.combine(
                end, datetime.max.time()))

        pag = _paged(query, CodCollection.created_at, page, per_page)
        return {
            "items": [c.to_dict() for c in pag.items],
            "total": pag.total,
            "page": pag.page,
            "pages": pag.pages,
            "per_page": per_page,
        }


@cod_ns.route("/collections/<int:shipment_id>/collect")
@cod_ns.param("shipment_id", "The shipment the money was collected against")
class CodCollect(Resource):
    @cod_ns.doc("collect_cod", security="Bearer")
    @cod_ns.response(201, "Collection recorded")
    @cod_ns.response(400, "Not a COD shipment, or the amount does not match")
    @cod_ns.response(409, "Already collected")
    @require_roles("rider", "agent", "admin", "super_admin")
    def post(self, shipment_id):
        """Record money taken at the door. The amount must equal the shipment's COD amount."""
        uid = int(get_jwt_identity())
        data = json_object()

        shipment = db.session.get(Shipment, shipment_id)
        if not shipment:
            cod_ns.abort(404, "Shipment not found")
        if not shipment.is_cod:
            cod_ns.abort(400, "This shipment is not a cash-on-delivery shipment")

        amount = number(data.get("amount"), "amount", minimum=0)
        if not _amount_matches(amount, shipment.cod_amount):
            cod_ns.abort(400, f"Amount must equal the shipment COD amount of {shipment.cod_amount}")

        mode = data.get("collection_mode", "cash")
        if mode not in CodCollection.MODES:
            cod_ns.abort(400, f"collection_mode must be one of: {', '.join(CodCollection.MODES)}")

        reference_no = text(data.get("reference_no", ""), "reference_no", 100)
        notes = text(data.get("notes", ""), "notes", 2000)

        collection = CodCollection.query.filter_by(shipment_id=shipment.id).first()
        if collection and collection.status != CodCollection.PENDING:
            cod_ns.abort(409, f"COD for this shipment is already {collection.status}")
        if collection is None:
            collection = CodCollection(shipment_id=shipment.id)
            db.session.add(collection)

        # Whitelisted writes only; everything else is derived from the shipment.
        collection.merchant_id = shipment.merchant_id or shipment.user_id
        collection.branch_id = (shipment.current_branch_id
                                or shipment.destination_branch_id
                                or shipment.origin_branch_id)
        collection.rider_id = shipment.rider_id
        collection.amount = round(float(shipment.cod_amount or 0.0), 2)
        collection.currency = shipment.cod_currency or "NPR"
        collection.collection_mode = mode
        collection.reference_no = reference_no or None
        collection.notes = notes or None
        collection.status = CodCollection.COLLECTED
        collection.collected_at = datetime.utcnow()

        shipment.cod_status = CodCollection.COLLECTED
        shipment.cod_collected_at = collection.collected_at

        db.session.commit()
        current_app.logger.info("COD collected for shipment %s by user %s", shipment.id, uid)
        return collection.to_dict(), 201


@cod_ns.route("/collections/<int:id>/deposit")
@cod_ns.param("id", "The collection identifier")
class CodDeposit(Resource):
    @cod_ns.doc("deposit_cod", security="Bearer")
    @cod_ns.response(200, "Deposit recorded")
    @cod_ns.response(400, "Collection is not in a depositable state")
    @require_roles("admin", "super_admin")
    def post(self, id):
        """Branch confirms the cash reached the branch, against a deposit reference."""
        data = json_object()
        collection = db.session.get(CodCollection, id)
        if not collection:
            cod_ns.abort(404, "Collection not found")
        if collection.status != CodCollection.COLLECTED:
            cod_ns.abort(400, f"Only a collected COD can be deposited; this one is {collection.status}")

        reference = text(data.get("deposit_reference", ""), "deposit_reference", 100, required=True)
        branch_id = _optional_int(data, "branch_id")

        collection.deposit_reference = reference
        collection.deposited_at = datetime.utcnow()
        collection.status = CodCollection.DEPOSITED
        if branch_id is not None:
            collection.branch_id = branch_id
        db.session.commit()
        return collection.to_dict()


@cod_ns.route("/collections/<int:id>/verify")
@cod_ns.param("id", "The collection identifier")
class CodVerify(Resource):
    @cod_ns.doc("verify_cod", security="Bearer")
    @cod_ns.response(200, "Collection verified")
    @cod_ns.response(400, "Nothing to verify yet")
    @require_roles("admin", "super_admin")
    def post(self, id):
        """Admin signs off that the recorded collection matches the cash."""
        uid = int(get_jwt_identity())
        data = request.get_json(silent=True) or {}
        collection = db.session.get(CodCollection, id)
        if not collection:
            cod_ns.abort(404, "Collection not found")
        if collection.status not in (CodCollection.COLLECTED, CodCollection.DEPOSITED,
                                     CodCollection.REMITTED):
            cod_ns.abort(400, f"A {collection.status} collection cannot be verified")

        collection.verified_by = uid
        collection.verified_at = datetime.utcnow()
        if data.get("notes") is not None:
            collection.notes = text(data.get("notes", ""), "notes", 2000) or None
        db.session.commit()
        return collection.to_dict()


@cod_ns.route("/summary")
class CodSummary(Resource):
    @cod_ns.doc("cod_summary", security="Bearer",
                params={"merchant_id": "Admin only; a merchant always sees their own"})
    @cod_ns.response(200, "Per-merchant outstanding totals")
    @jwt_required()
    def get(self):
        """Per-merchant COD position: collected but not yet remitted, broken down by status."""
        user = _current_user()

        query = db.session.query(
            CodCollection.merchant_id,
            CodCollection.status,
            db.func.count(CodCollection.id),
            db.func.coalesce(db.func.sum(CodCollection.amount), 0.0),
        )
        if _is_admin(user):
            merchant_id = request.args.get("merchant_id", type=int)
            if merchant_id:
                query = query.filter(CodCollection.merchant_id == merchant_id)
        else:
            query = query.filter(CodCollection.merchant_id == user.id)

        rows = query.group_by(CodCollection.merchant_id, CodCollection.status).all()

        merchants = {}
        for merchant_id, status, count, total in rows:
            entry = merchants.setdefault(merchant_id, {
                "merchant_id": merchant_id,
                "by_status": {s: {"count": 0, "amount": 0.0} for s in CodCollection.STATUSES},
                "outstanding_amount": 0.0,
                "outstanding_count": 0,
                "remitted_amount": 0.0,
            })
            bucket = entry["by_status"].setdefault(status, {"count": 0, "amount": 0.0})
            bucket["count"] += int(count or 0)
            bucket["amount"] = round(bucket["amount"] + float(total or 0.0), 2)
            if status in OUTSTANDING_STATUSES:
                entry["outstanding_count"] += int(count or 0)
                entry["outstanding_amount"] = round(
                    entry["outstanding_amount"] + float(total or 0.0), 2)
            elif status == CodCollection.REMITTED:
                entry["remitted_amount"] = round(
                    entry["remitted_amount"] + float(total or 0.0), 2)

        items = sorted(merchants.values(), key=lambda e: e["outstanding_amount"], reverse=True)
        return {
            "merchants": items,
            "total_outstanding": round(sum(e["outstanding_amount"] for e in items), 2),
            "total_remitted": round(sum(e["remitted_amount"] for e in items), 2),
            "merchant_count": len(items),
        }


# ── remittances ──────────────────────────────────────────────────────────────

def _remittance_or_404(id):
    remittance = db.session.get(CodRemittance, id)
    if not remittance:
        cod_ns.abort(404, "Remittance not found")
    return remittance


def _guard_immutable(remittance):
    if remittance.status == CodRemittance.PAID:
        cod_ns.abort(400, "A paid remittance cannot be changed")


@cod_ns.route("/remittances")
class CodRemittanceList(Resource):
    @cod_ns.doc("list_remittances", security="Bearer",
                params={"status": "Filter by remittance status",
                        "merchant_id": "Admin only; a merchant always sees their own"})
    @cod_ns.response(200, "Paginated remittances")
    @jwt_required()
    def get(self):
        """List remittances. Admins see all; a merchant sees only their own."""
        user = _current_user()
        page, per_page = pagination()

        query = CodRemittance.query
        if _is_admin(user):
            merchant_id = request.args.get("merchant_id", type=int)
            if merchant_id:
                query = query.filter(CodRemittance.merchant_id == merchant_id)
        else:
            query = query.filter(CodRemittance.merchant_id == user.id)

        status = request.args.get("status")
        if status:
            if status not in CodRemittance.STATUSES:
                raise BadRequest(f"status must be one of: {', '.join(CodRemittance.STATUSES)}")
            query = query.filter(CodRemittance.status == status)

        pag = _paged(query, CodRemittance.created_at, page, per_page)
        return {
            "items": [r.to_dict() for r in pag.items],
            "total": pag.total,
            "page": pag.page,
            "pages": pag.pages,
            "per_page": per_page,
        }

    @cod_ns.doc("create_remittance", security="Bearer")
    @cod_ns.response(201, "Remittance drafted")
    @cod_ns.response(400, "No eligible collections, or bad period")
    @require_roles("admin", "super_admin")
    def post(self):
        """Draft a remittance for one merchant over a period.

        Gathers every deposited collection in the window that is not already
        attached to a remittance, then derives the totals with
        ``recalculate()``. A client-supplied ``total_collected`` or
        ``net_payable`` is ignored.
        """
        data = json_object()

        merchant_id = _optional_int(data, "merchant_id")
        if merchant_id is None:
            raise BadRequest("merchant_id is required")
        merchant = db.session.get(User, merchant_id)
        if not merchant:
            raise BadRequest("merchant_id does not exist")

        period_start = _parse_date(data.get("period_start"), "period_start")
        period_end = _parse_date(data.get("period_end"), "period_end")
        if period_end < period_start:
            raise BadRequest("period_end cannot be before period_start")

        # Deductions are an operator input; the totals they feed are not.
        service_fee = round(float(number(data.get("service_fee", 0), "service_fee")), 2)
        tax_amount = round(float(number(data.get("tax_amount", 0), "tax_amount")), 2)
        adjustment = data.get("adjustment", 0)
        if isinstance(adjustment, bool) or not isinstance(adjustment, (int, float)):
            raise BadRequest("adjustment must be a number")
        adjustment = round(float(adjustment), 2)

        eligible = CodCollection.query.filter(
            CodCollection.merchant_id == merchant_id,
            CodCollection.status == CodCollection.DEPOSITED,
            CodCollection.remittance_id.is_(None),
            CodCollection.collected_at >= datetime.combine(period_start, datetime.min.time()),
            CodCollection.collected_at <= datetime.combine(period_end, datetime.max.time()),
        ).all()
        if not eligible:
            raise BadRequest("No deposited, unremitted collections for that merchant and period")

        remittance = CodRemittance(
            remittance_no=CodRemittance.generate_number(),
            merchant_id=merchant_id,
            branch_id=_optional_int(data, "branch_id") or eligible[0].branch_id,
            period_start=period_start,
            period_end=period_end,
            service_fee=service_fee,
            tax_amount=tax_amount,
            adjustment=adjustment,
            currency=eligible[0].currency or "NPR",
            status=CodRemittance.DRAFT,
            payment_method=text(data.get("payment_method", ""), "payment_method", 30) or None,
            bank_account=text(data.get("bank_account", ""), "bank_account", 120) or None,
            notes=text(data.get("notes", ""), "notes", 2000) or None,
        )
        db.session.add(remittance)
        db.session.flush()

        for collection in eligible:
            remittance.collections.append(collection)

        remittance.recalculate()
        db.session.commit()
        return remittance.to_dict(include_collections=True), 201


@cod_ns.route("/remittances/<int:id>")
@cod_ns.param("id", "The remittance identifier")
class CodRemittanceDetail(Resource):
    @cod_ns.doc("get_remittance", security="Bearer")
    @cod_ns.response(200, "Remittance with its collections")
    @cod_ns.response(403, "Access denied")
    @jwt_required()
    def get(self, id):
        """Remittance detail. The merchant it belongs to, or an admin."""
        user = _current_user()
        remittance = _remittance_or_404(id)
        if remittance.merchant_id != user.id and not _is_admin(user):
            cod_ns.abort(403, "Access denied")
        return remittance.to_dict(include_collections=True)


@cod_ns.route("/remittances/<int:id>/approve")
@cod_ns.param("id", "The remittance identifier")
class CodRemittanceApprove(Resource):
    @cod_ns.doc("approve_remittance", security="Bearer")
    @cod_ns.response(200, "Remittance approved")
    @cod_ns.response(403, "Only super_admin may approve")
    @require_roles("super_admin")
    def post(self, id):
        """Approve a drafted remittance. Totals are re-derived before signing off."""
        uid = int(get_jwt_identity())
        remittance = _remittance_or_404(id)
        _guard_immutable(remittance)
        if remittance.status != CodRemittance.DRAFT:
            cod_ns.abort(400, f"Only a draft remittance can be approved; this one is {remittance.status}")

        remittance.recalculate()
        remittance.status = CodRemittance.APPROVED
        remittance.approved_by = uid
        remittance.approved_at = datetime.utcnow()
        db.session.commit()
        return remittance.to_dict()


@cod_ns.route("/remittances/<int:id>/pay")
@cod_ns.param("id", "The remittance identifier")
class CodRemittancePay(Resource):
    @cod_ns.doc("pay_remittance", security="Bearer")
    @cod_ns.response(200, "Remittance paid; collections marked remitted")
    @cod_ns.response(403, "Only super_admin may pay")
    @require_roles("super_admin")
    def post(self, id):
        """Mark an approved remittance paid and settle every attached collection."""
        data = request.get_json(silent=True) or {}
        remittance = _remittance_or_404(id)
        _guard_immutable(remittance)
        if remittance.status != CodRemittance.APPROVED:
            cod_ns.abort(400, f"Only an approved remittance can be paid; this one is {remittance.status}")

        payment_method = text(data.get("payment_method", ""), "payment_method", 30)
        payment_reference = text(data.get("payment_reference", ""), "payment_reference", 100,
                                 required=True)

        remittance.recalculate()
        remittance.status = CodRemittance.PAID
        remittance.paid_at = datetime.utcnow()
        remittance.payment_reference = payment_reference
        if payment_method:
            remittance.payment_method = payment_method

        for collection in remittance.collections:
            collection.status = CodCollection.REMITTED

        db.session.commit()
        return remittance.to_dict(include_collections=True)
