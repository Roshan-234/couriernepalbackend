# app/shipments/routes.py

from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.auth.utils import require_roles
from app.extensions import db
from app.models.shipment import Shipment
from app.models.parcel import Parcel

ship_ns = Namespace("shipments", description="Shipments & Parcels")

# Swagger serializers
parcel_field = fields.Nested(ship_ns.model("ParcelBrief", {
    "id":       fields.Integer(),
    "weight_kg":fields.Float(),
    "description":fields.String(),
    "created_at": fields.DateTime()
}))

shipment_out = ship_ns.model("ShipmentOut", {
    "id":          fields.Integer(),
    "tracking_no": fields.String(),
    "user_id":     fields.Integer(),
    "service_id":  fields.Integer(),
    "created_at":  fields.DateTime(),
    "parcels":     fields.List(parcel_field)
})

@ship_ns.route("/")
class ShipmentList(Resource):
    @ship_ns.doc(security="Bearer")
    @ship_ns.marshal_list_with(shipment_out)
    @require_roles("super_admin", "admin", "manager")
    def get(self):
        """List **all** shipments (admin/manager only)"""
        return Shipment.query.all()

@ship_ns.route("/<int:id>")
class ShipmentDetail(Resource):
    @ship_ns.doc(security="Bearer")
    @ship_ns.marshal_with(shipment_out)
    @require_roles("super_admin", "admin", "manager")
    def get(self, id):
        """Get details of any shipment"""
        return Shipment.query.get_or_404(id)
@ship_ns.route("/my")


class MyShipments(Resource):
    @ship_ns.doc(security="Bearer")
    @ship_ns.marshal_list_with(shipment_out)
    @jwt_required()
    def get(self):
        """Customers see only THEIR shipments"""
        from flask_jwt_extended import get_jwt_identity
        user_id = get_jwt_identity()
        return Shipment.query.filter_by(user_id=user_id).all()
