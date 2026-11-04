# app/shipments/routes.py

import uuid
import math
from datetime import datetime, timedelta
from flask import current_app, request
from sqlalchemy.orm import selectinload
from app.validation import shipment_data, json_object, tracking_update
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.auth.utils import require_roles
from app.extensions import db
from app.models.shipment import Shipment
from app.notifications.dispatch import (notify_shipment_created,
                                        notify_status_change)
from app.models.parcel import Parcel
from app.models.tracking_event import TrackingEvent
from app.shipment_status import (
    AGENT_STATUSES, DELIVERED, can_transition, label as status_label, next_statuses,
)

ship_ns = Namespace("shipments", description="Shipments & Parcels")

# ── Swagger models ────────────────────────────────────────────────────────────

parcel_in = ship_ns.model("ParcelIn", {
    "weight_kg":   fields.Float(required=True),
    "description": fields.String(),
})

shipment_in = ship_ns.model("ShipmentIn", {
    # Route
    "origin":               fields.String(required=True),
    "destination":          fields.String(required=True),
    "origin_country":       fields.String(default="Nepal"),
    "destination_country":  fields.String(),
    # Sender
    "sender_name":          fields.String(),
    "sender_address":       fields.String(),
    "sender_phone":         fields.String(),
    "sender_email":         fields.String(),
    # Receiver
    "receiver_name":        fields.String(),
    "receiver_address":     fields.String(),
    "receiver_phone":       fields.String(),
    "receiver_email":       fields.String(),
    # Cargo
    "declared_value":       fields.Float(default=0.0),
    "currency":             fields.String(default="USD"),
    "incoterms":            fields.String(),
    "total_weight_kg":      fields.Float(default=0.0),
    "total_packages":       fields.Integer(default=1),
    # Meta
    "service_id":           fields.Integer(),
    "notes":                fields.String(),
    "parcels":              fields.List(fields.Nested(parcel_in)),
    # Logistics. Charges are absent on purpose: the server prices the
    # shipment, so a client cannot name its own freight total.
    "origin_district":      fields.String(description="One of Nepal's 77 districts"),
    "destination_district": fields.String(),
    "origin_branch_id":     fields.Integer(),
    "destination_branch_id": fields.Integer(),
    "service_level":        fields.String(enum=["express", "standard", "economy", "same_day"]),
    "length_cm":            fields.Float(),
    "width_cm":             fields.Float(),
    "height_cm":            fields.Float(),
    "contents_description": fields.String(),
    "is_fragile":           fields.Boolean(default=False),
    "is_insured":           fields.Boolean(default=False),
    "insurance_amount":     fields.Float(default=0.0),
    "is_cod":               fields.Boolean(default=False),
    "cod_amount":           fields.Float(default=0.0),
    "cod_currency":         fields.String(default="NPR"),
    "payment_mode":         fields.String(enum=["prepaid", "cod", "credit"]),
    "reference_no":         fields.String(description="The merchant's own order id"),
})

parcel_out = ship_ns.model("ParcelOut", {
    "id":          fields.Integer(),
    "weight_kg":   fields.Float(),
    "description": fields.String(),
    "created_at":  fields.DateTime(),
})

tracking_event_out = ship_ns.model("TrackingEventOut", {
    "id":          fields.Integer(),
    "status":      fields.String(),
    "location":    fields.String(),
    "description": fields.String(),
    "timestamp":   fields.DateTime(),
})

service_out = ship_ns.model("ServiceOut", {
    "id":   fields.Integer(),
    "name": fields.String(),
})

shipment_out = ship_ns.model("ShipmentOut", {
    "id":                   fields.Integer(),
    "tracking_no":          fields.String(),
    "status":               fields.String(),
    # Route
    "origin":               fields.String(),
    "destination":          fields.String(),
    "origin_country":       fields.String(),
    "destination_country":  fields.String(),
    # Sender
    "sender_name":          fields.String(),
    "sender_address":       fields.String(),
    "sender_phone":         fields.String(),
    "sender_email":         fields.String(),
    # Receiver
    "receiver_name":        fields.String(),
    "receiver_address":     fields.String(),
    "receiver_phone":       fields.String(),
    "receiver_email":       fields.String(),
    # Cargo
    "declared_value":       fields.Float(),
    "currency":             fields.String(),
    "incoterms":            fields.String(),
    "total_weight_kg":      fields.Float(),
    "total_packages":       fields.Integer(),
    # Meta
    "notes":                fields.String(),
    "user_id":              fields.Integer(),
    "service_id":           fields.Integer(),
    "service":              fields.Nested(service_out, allow_null=True),
    "estimated_delivery":   fields.DateTime(),
    "created_at":           fields.DateTime(),
    "updated_at":           fields.DateTime(),
    "parcels":              fields.List(fields.Nested(parcel_out)),
    "events":               fields.List(fields.Nested(tracking_event_out)),
})

status_update = ship_ns.model("StatusUpdate", {
    "status":      fields.String(required=True),
    "location":    fields.String(),
    "description": fields.String(),
})


# ── helpers ───────────────────────────────────────────────────────────────────

def _generate_tracking_no():
    return "MNL" + uuid.uuid4().hex[:9].upper()


def _is_admin(user_id):
    from app.models.user import User
    user = db.session.get(User, user_id)
    return bool(user and user.is_admin)


def _rider_for(user_id):
    """The rider profile belonging to a user, if they have one."""
    from app.models.rider import Rider
    return Rider.query.filter_by(user_id=user_id).first()


def _require_visible(shipment, user_id):
    """Allow the owner, the merchant who booked it, the assigned rider, or an admin."""
    if _is_admin(user_id):
        return
    if shipment.user_id == user_id or shipment.merchant_id == user_id:
        return
    rider = _rider_for(user_id)
    if rider and shipment.rider_id == rider.id:
        return
    ship_ns.abort(403, "Access denied")


def _build_shipment(data: dict, user_id: int) -> Shipment:
    """Create a Shipment instance from request data."""
    tracking_no = _generate_tracking_no()
    while Shipment.query.filter_by(tracking_no=tracking_no).first():
        tracking_no = _generate_tracking_no()

    return Shipment(
        tracking_no=tracking_no,
        user_id=user_id,
        service_id=data.get("service_id"),
        status="pending",
        # Route
        origin=data.get("origin", ""),
        destination=data.get("destination", ""),
        origin_country=data.get("origin_country", "Nepal"),
        destination_country=data.get("destination_country"),
        # Sender
        sender_name=data.get("sender_name"),
        sender_address=data.get("sender_address"),
        sender_phone=data.get("sender_phone"),
        sender_email=data.get("sender_email"),
        # Receiver
        receiver_name=data.get("receiver_name"),
        receiver_address=data.get("receiver_address"),
        receiver_phone=data.get("receiver_phone"),
        receiver_email=data.get("receiver_email"),
        # Cargo
        declared_value=data.get("declared_value", 0.0),
        currency=data.get("currency", "USD"),
        incoterms=data.get("incoterms"),
        total_weight_kg=data.get("total_weight_kg", 0.0),
        total_packages=data.get("total_packages", 1),
        # Meta
        notes=data.get("notes", ""),
        estimated_delivery=None,
        # Logistics. Validated in app.validation.logistics_fields, which also
        # canonicalises district names so rate lanes group correctly.
        origin_district=data.get("origin_district"),
        destination_district=data.get("destination_district"),
        origin_branch_id=data.get("origin_branch_id"),
        destination_branch_id=data.get("destination_branch_id"),
        service_level=data.get("service_level") or "standard",
        length_cm=data.get("length_cm"),
        width_cm=data.get("width_cm"),
        height_cm=data.get("height_cm"),
        contents_description=data.get("contents_description"),
        is_fragile=bool(data.get("is_fragile", False)),
        is_insured=bool(data.get("is_insured", False)),
        insurance_amount=data.get("insurance_amount", 0.0) or 0.0,
        is_cod=bool(data.get("is_cod", False)),
        cod_amount=data.get("cod_amount", 0.0) or 0.0,
        cod_currency=data.get("cod_currency") or "NPR",
        cod_status="pending" if data.get("is_cod") else "not_applicable",
        payment_mode=data.get("payment_mode") or ("cod" if data.get("is_cod") else "prepaid"),
        reference_no=data.get("reference_no"),
    )


def _price(shipment):
    """Price a shipment from the rate engine and store the breakdown.

    Pricing is advisory at booking time: a route with no published lane is a
    tariff gap, not a reason to refuse the parcel. A clerk with a customer in
    front of them accepts it and prices it by hand afterwards.

    What the shipment must never do is *look* priced when it is not. The
    charge columns default to 0.0, so an unrated shipment was previously
    indistinguishable from a genuinely free one and an invoice could be
    raised against it for nothing. With no international tariff loaded that
    described every shipment on the company's main business. So every path
    out of here sets `pricing_status`, and only a matched lane sets `priced`.
    """
    from app.rating import quote

    try:
        result = quote(
            origin_district=shipment.origin_district,
            destination_district=shipment.destination_district,
            destination_country=shipment.destination_country,
            weight_kg=shipment.total_weight_kg or 0.0,
            length_cm=shipment.length_cm,
            width_cm=shipment.width_cm,
            height_cm=shipment.height_cm,
            service_level=shipment.service_level or "standard",
            is_cod=bool(shipment.is_cod),
            cod_amount=shipment.cod_amount or 0.0,
            is_fragile=bool(shipment.is_fragile),
            is_insured=bool(shipment.is_insured),
            declared_value=shipment.declared_value or 0.0,
            merchant_id=shipment.merchant_id,
        )
    except Exception:
        # A pricing fault must never lose the booking — but it must not pass
        # for a price either.
        current_app.logger.exception("Pricing failed for shipment %s",
                                     getattr(shipment, "tracking_no", "(new)"))
        shipment.pricing_status = Shipment.PRICING_FAILED
        shipment.pricing_note = "The pricing engine errored; quote this by hand."
        return None

    if result.get("status") != "ok":
        shipment.pricing_status = Shipment.UNRATED
        shipment.pricing_note = (result.get("message")
                                 or "No tariff covers this route.")[:255]
        return result

    shipment.pricing_status = Shipment.PRICED
    shipment.pricing_note = None

    weight = result.get("weight") or {}
    shipment.volumetric_weight_kg = weight.get("volumetric_weight_kg") or 0.0
    shipment.chargeable_weight_kg = weight.get("chargeable_weight_kg") or 0.0
    shipment.freight_charge = result.get("freight") or 0.0
    shipment.surcharge_total = result.get("surcharge_total") or 0.0
    shipment.tax_amount = result.get("tax") or 0.0
    shipment.total_charge = result.get("total") or 0.0
    if result.get("currency"):
        shipment.currency = result["currency"]

    transit = result.get("transit_days") or {}
    if transit.get("max") is not None:
        shipment.estimated_delivery = datetime.utcnow() + timedelta(days=int(transit["max"]))
        shipment.sla_due_at = shipment.estimated_delivery
    return result


# ── /  ────────────────────────────────────────────────────────────────────────

@ship_ns.route("/")
class ShipmentList(Resource):

    @require_roles("super_admin", "admin")
    def get(self):
        """List all shipments (admin only)."""
        rows = Shipment.query.options(selectinload(Shipment.service), selectinload(Shipment.parcels), selectinload(Shipment.events)).order_by(Shipment.created_at.desc()).all()
        return [row.to_dict(include_relations=True) for row in rows]

    # The model documents the payload but does not gate it: its jsonschema
    # rejects an explicit null, so an international booking that sends
    # `destination_district: null` would 400 on a field it is right to omit.
    # shipment_data() is the real validator and checks more besides.
    @ship_ns.expect(shipment_in)
    @jwt_required()
    def post(self):
        """Create a new shipment."""
        user_id = int(get_jwt_identity())
        data = shipment_data(json_object())

        shipment = _build_shipment(data, user_id)
        _price(shipment)
        db.session.add(shipment)
        db.session.flush()

        # Optional parcels
        for p in data.get("parcels", []):
            db.session.add(Parcel(
                shipment_id=shipment.id,
                weight_kg=p.get("weight_kg", 0),
                description=p.get("description", ""),
            ))

        # Initial tracking event
        db.session.add(TrackingEvent(
            shipment_id=shipment.id,
            status="pending",
            location=data.get("origin", ""),
            description="Shipment created and awaiting pickup.",
        ))
        db.session.commit()
        # After the commit: a customer must never be told about a booking
        # that then failed to save.
        notify_shipment_created(shipment)
        # to_dict rather than a marshal model: the marshal list predates the
        # logistics fields and would silently strip COD, districts, charges
        # and the status label from every response.
        return shipment.to_dict(include_relations=True), 201


# ── /<int:id>  ────────────────────────────────────────────────────────────────

@ship_ns.route("/<int:id>")
class ShipmentDetail(Resource):

    @jwt_required()
    def get(self, id):
        """Get a shipment by ID (owner, assigned rider, or admin)."""
        shipment = db.session.get(Shipment, id) or ship_ns.abort(404, "Shipment not found")
        _require_visible(shipment, int(get_jwt_identity()))
        return shipment.to_dict(include_relations=True)

    @ship_ns.expect(status_update, validate=True)
    @require_roles("super_admin", "admin", "agent", "rider")
    def put(self, id):
        """Update shipment status and add a tracking event."""
        shipment = db.session.get(Shipment, id) or ship_ns.abort(404, "Shipment not found")
        data = tracking_update(json_object())
        new_status = data.get("status")
        if not new_status or not new_status.strip():
            ship_ns.abort(400, 'A shipment status is required')

        user_id = int(get_jwt_identity())
        if not _is_admin(user_id):
            # A delivery agent may only touch work assigned to them. Without
            # this any agent could mark a stranger's parcel delivered.
            rider = _rider_for(user_id)
            if not rider or shipment.rider_id != rider.id:
                ship_ns.abort(403, "This shipment is not assigned to you")
            if new_status not in AGENT_STATUSES:
                ship_ns.abort(403, "That status may only be set by operations")

        if not can_transition(shipment.status, new_status):
            ship_ns.abort(409, f"Cannot move a shipment from "
                               f"'{status_label(shipment.status)}' to "
                               f"'{status_label(new_status)}'")

        previous_status = shipment.status
        shipment.status = new_status
        if new_status == DELIVERED and not shipment.delivered_at:
            shipment.delivered_at = datetime.utcnow()
        db.session.add(TrackingEvent(
            shipment_id=shipment.id,
            status=new_status,
            location=data.get("location", ""),
            description=data.get("description", ""),
        ))
        db.session.commit()
        notify_status_change(shipment, previous_status)
        return {"msg": "Status updated", "status": shipment.status,
                "status_label": status_label(shipment.status),
                "next_statuses": next_statuses(shipment.status),
                "tracking_no": shipment.tracking_no}

    @require_roles("super_admin", "admin")
    def delete(self, id):
        """Delete a shipment (admin only)."""
        shipment = Shipment.query.get_or_404(id)
        db.session.delete(shipment)
        db.session.commit()
        return '', 204


# ── /tracking/<tracking_no>  ─────────────────────────────────────────────────

@ship_ns.route("/tracking/<string:tracking_no>")
class ShipmentByTracking(Resource):

    @jwt_required()
    def get(self, tracking_no):
        """Get a shipment by tracking number (owner, assigned rider, admin)."""
        shipment = Shipment.query.filter_by(tracking_no=tracking_no).first_or_404()
        _require_visible(shipment, int(get_jwt_identity()))
        return shipment.to_dict(include_relations=True)


# ── /my  ─────────────────────────────────────────────────────────────────────

@ship_ns.route("/my")
class MyShipments(Resource):

    @jwt_required()
    def get(self):
        """List the current user's shipments."""
        user_id = int(get_jwt_identity())
        rows = Shipment.query.options(selectinload(Shipment.service), selectinload(Shipment.parcels), selectinload(Shipment.events)).filter_by(user_id=user_id) \
                             .order_by(Shipment.created_at.desc()).all()
        return [row.to_dict(include_relations=True) for row in rows]


@ship_ns.route('/page')
class ShipmentPage(Resource):
    @jwt_required()
    def get(self):
        from app.validation import pagination
        from app.models.user import User
        from sqlalchemy import func, case, or_
        from datetime import date as date_type
        from app.shipment_status import (
            DELIVERY_ATTEMPTED, IN_TRANSIT, ON_HOLD, OUT_FOR_DELIVERY,
            PENDING, is_valid,
        )
        page, size = pagination()
        uid = int(get_jwt_identity())
        user = db.session.get(User, uid)
        query = Shipment.query
        if request.args.get('all') == '1':
            if not user.is_admin:
                ship_ns.abort(403, 'Admin privileges required')
        else:
            query = query.filter(Shipment.user_id == uid)

        # Filters are applied before the counts so the summary describes the
        # list the operator is actually looking at, not the whole table.
        args = request.args
        status = args.get('status', '').strip()
        if status:
            if not is_valid(status):
                ship_ns.abort(400, 'Unknown shipment status')
            query = query.filter(Shipment.status == status)
        # The queue a clerk works through: everything still carrying a
        # placeholder zero instead of a price.
        if args.get('needs_pricing') in ('1', 'true'):
            query = query.filter(Shipment.pricing_status != Shipment.PRICED)
        elif args.get('needs_pricing') in ('0', 'false'):
            query = query.filter(Shipment.pricing_status == Shipment.PRICED)
        pricing_status = args.get('pricing_status', '').strip()
        if pricing_status:
            if pricing_status not in Shipment.PRICING_STATUSES:
                ship_ns.abort(400, 'Unknown pricing status')
            query = query.filter(Shipment.pricing_status == pricing_status)
        if args.get('is_cod') in ('1', 'true'):
            query = query.filter(Shipment.is_cod.is_(True))
        elif args.get('is_cod') in ('0', 'false'):
            query = query.filter(Shipment.is_cod.is_(False))
        for param, column in (('payment_status', Shipment.payment_status),
                              ('cod_status', Shipment.cod_status),
                              ('service_level', Shipment.service_level),
                              ('origin_district', Shipment.origin_district),
                              ('destination_district', Shipment.destination_district)):
            value = args.get(param, '').strip()
            if value:
                query = query.filter(column == value)
        for param, column in (('rider_id', Shipment.rider_id),
                              ('origin_branch_id', Shipment.origin_branch_id),
                              ('destination_branch_id', Shipment.destination_branch_id),
                              ('merchant_id', Shipment.merchant_id)):
            value = args.get(param)
            if value:
                try:
                    query = query.filter(column == int(value))
                except ValueError:
                    ship_ns.abort(400, f'{param} must be an integer')
        # "At this branch" is what an operator means: anything the branch
        # sends, receives or is currently holding.
        branch_id = args.get('branch_id')
        if branch_id:
            try:
                branch_id = int(branch_id)
            except ValueError:
                ship_ns.abort(400, 'branch_id must be an integer')
            query = query.filter(or_(Shipment.origin_branch_id == branch_id,
                                     Shipment.destination_branch_id == branch_id,
                                     Shipment.current_branch_id == branch_id))
        for param, op in (('date_from', 'ge'), ('date_to', 'le')):
            value = args.get(param)
            if not value:
                continue
            try:
                parsed = date_type.fromisoformat(value)
            except ValueError:
                ship_ns.abort(400, f'{param} must be an ISO date (YYYY-MM-DD)')
            column = Shipment.created_at
            query = query.filter(column >= parsed if op == 'ge'
                                 else column < parsed + timedelta(days=1))

        search = args.get('search', '').strip()[:100]
        if search:
            # Escape LIKE wildcards so user input is literal.
            escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            query = query.filter(or_(Shipment.tracking_no.ilike(escaped + '%', escape='\\'),
                Shipment.destination.ilike('%' + escaped + '%', escape='\\'),
                Shipment.origin.ilike('%' + escaped + '%', escape='\\'),
                Shipment.reference_no.ilike(escaped + '%', escape='\\'),
                Shipment.receiver_name.ilike('%' + escaped + '%', escape='\\'),
                Shipment.status == search))

        # Aggregate in SQL rather than downloading every shipment into the
        # browser. 'needs_attention' is what the dashboard actually acts on.
        def tally(condition):
            return func.sum(case((condition, 1), else_=0))

        counts = query.with_entities(
            func.count(Shipment.id),
            tally(Shipment.status == IN_TRANSIT),
            tally(Shipment.status == DELIVERED),
            tally(Shipment.status == PENDING),
            tally(Shipment.status == OUT_FOR_DELIVERY),
            tally(Shipment.status.in_((DELIVERY_ATTEMPTED, ON_HOLD))),
            tally(Shipment.is_cod.is_(True)),
            # Shipments carrying a placeholder zero rather than a price. This
            # is a revenue number, not a tidiness one: each of these can be
            # invoiced for nothing until somebody quotes it.
            tally(Shipment.pricing_status != Shipment.PRICED),
            func.coalesce(func.sum(case((Shipment.is_cod.is_(True), Shipment.cod_amount), else_=0)), 0),
        ).one()

        result = query.order_by(Shipment.created_at.desc(), Shipment.id.desc()).paginate(page=page, per_page=size, error_out=False)
        keys = ('total', 'in_transit', 'delivered', 'pending',
                'out_for_delivery', 'needs_attention', 'cod_count',
                'needs_pricing')
        summary = {key: int(value or 0) for key, value in zip(keys, counts)}
        summary['cod_amount'] = round(float(counts[-1] or 0), 2)
        return {'items': [shipment.to_dict() for shipment in result.items], 'total': result.total,
                'page': page, 'pages': result.pages, 'per_page': size, 'summary': summary}
