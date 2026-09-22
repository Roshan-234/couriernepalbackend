# app/shipments/routes.py

import uuid
from datetime import datetime, timedelta
from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.auth.utils import require_roles
from app.extensions import db
from app.models.shipment import Shipment
from app.models.parcel import Parcel
from app.models.tracking_event import TrackingEvent

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
        estimated_delivery=datetime.utcnow() + timedelta(days=7),
    )


# ── /  ────────────────────────────────────────────────────────────────────────

@ship_ns.route("/")
class ShipmentList(Resource):

    @ship_ns.marshal_list_with(shipment_out)
    @require_roles("super_admin", "admin")
    def get(self):
        """List all shipments (admin only)."""
        return Shipment.query.order_by(Shipment.created_at.desc()).all()

    @ship_ns.expect(shipment_in)
    @ship_ns.marshal_with(shipment_out, code=201)
    @jwt_required()
    def post(self):
        """Create a new shipment."""
        user_id = int(get_jwt_identity())
        data = request.get_json(force=True)

        shipment = _build_shipment(data, user_id)
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
        return shipment, 201


# ── /<int:id>  ────────────────────────────────────────────────────────────────

@ship_ns.route("/<int:id>")
class ShipmentDetail(Resource):

    @ship_ns.marshal_with(shipment_out)
    @jwt_required()
    def get(self, id):
        """Get a shipment by ID (owner or admin)."""
        user_id = int(get_jwt_identity())
        shipment = Shipment.query.get_or_404(id)
        from app.models.user import User
        user = User.query.get(user_id)
        roles = [r.name for r in user.roles]
        if "super_admin" not in roles and "admin" not in roles and shipment.user_id != user_id:
            ship_ns.abort(403, "Access denied")
        return shipment

    @ship_ns.expect(status_update)
    @require_roles("super_admin", "admin", "agent")
    def put(self, id):
        """Update shipment status and add a tracking event."""
        shipment = Shipment.query.get_or_404(id)
        data = request.get_json(force=True)
        new_status = data.get("status")
        if new_status:
            shipment.status = new_status
            db.session.add(TrackingEvent(
                shipment_id=shipment.id,
                status=new_status,
                location=data.get("location", ""),
                description=data.get("description", ""),
            ))
        db.session.commit()
        return {"msg": "Status updated", "status": shipment.status, "tracking_no": shipment.tracking_no}

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

    @ship_ns.marshal_with(shipment_out)
    @jwt_required()
    def get(self, tracking_no):
        """Get a shipment by tracking number (owner or admin)."""
        user_id = int(get_jwt_identity())
        shipment = Shipment.query.filter_by(tracking_no=tracking_no).first_or_404()
        from app.models.user import User
        user = User.query.get(user_id)
        roles = [r.name for r in user.roles]
        if "super_admin" not in roles and "admin" not in roles and shipment.user_id != user_id:
            ship_ns.abort(403, "Access denied")
        return shipment


# ── /my  ─────────────────────────────────────────────────────────────────────

@ship_ns.route("/my")
class MyShipments(Resource):

    @ship_ns.marshal_list_with(shipment_out)
    @jwt_required()
    def get(self):
        """List the current user's shipments."""
        user_id = int(get_jwt_identity())
        return Shipment.query.filter_by(user_id=user_id) \
                             .order_by(Shipment.created_at.desc()).all()
