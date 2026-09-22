from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.extensions import db
from app.models.tracking_provider import TrackingProvider
from app.auth.utils import require_roles

tp_ns = Namespace("tracking-providers", description="Courier tracking provider management")

provider_public = tp_ns.model("ProviderPublic", {
    "id": fields.Integer(), "name": fields.String(),
    "code": fields.String(), "logo_url": fields.String(),
    "instructions": fields.String(), "is_active": fields.Boolean(),
    "supported_countries": fields.String(),
})
provider_admin = tp_ns.model("ProviderAdmin", {
    "id": fields.Integer(), "name": fields.String(), "code": fields.String(),
    "api_url_template": fields.String(), "api_key": fields.String(),
    "webhook_url": fields.String(), "logo_url": fields.String(),
    "instructions": fields.String(), "is_active": fields.Boolean(),
    "supported_countries": fields.String(),
})
provider_input = tp_ns.model("ProviderInput", {
    "name": fields.String(required=True), "code": fields.String(required=True),
    "api_url_template": fields.String(), "api_key": fields.String(),
    "webhook_url": fields.String(), "logo_url": fields.String(),
    "instructions": fields.String(), "is_active": fields.Boolean(),
    "supported_countries": fields.String(),
})

@tp_ns.route("/")
class ProviderList(Resource):
    @tp_ns.marshal_list_with(provider_public)
    def get(self):
        """List all active tracking providers (public)"""
        return TrackingProvider.query.filter_by(is_active=True).all()

    @tp_ns.doc(security="Bearer")
    @tp_ns.expect(provider_input)
    @require_roles("super_admin")
    def post(self):
        """Create a tracking provider (admin only)"""
        data = request.get_json()
        if TrackingProvider.query.filter_by(code=data["code"]).first():
            return {"msg": "Provider code already exists"}, 409
        p = TrackingProvider(**data)
        db.session.add(p)
        db.session.commit()
        return p.to_dict(include_secrets=True), 201

@tp_ns.route("/all")
class ProviderListAdmin(Resource):
    @tp_ns.doc(security="Bearer")
    @require_roles("super_admin")
    def get(self):
        """List ALL providers including inactive (admin only)"""
        return [p.to_dict(include_secrets=True) for p in TrackingProvider.query.all()]

@tp_ns.route("/<string:code>")
class ProviderDetail(Resource):
    def get(self, code):
        """Get provider by code (public, no secrets)"""
        p = TrackingProvider.query.filter_by(code=code).first_or_404()
        return p.to_dict()

    @tp_ns.doc(security="Bearer")
    @tp_ns.expect(provider_input)
    @require_roles("super_admin")
    def put(self, code):
        """Update provider (admin only)"""
        p = TrackingProvider.query.filter_by(code=code).first_or_404()
        data = request.get_json()
        for k, v in data.items():
            if hasattr(p, k):
                setattr(p, k, v)
        db.session.commit()
        return p.to_dict(include_secrets=True)

    @tp_ns.doc(security="Bearer")
    @require_roles("super_admin")
    def delete(self, code):
        """Deactivate provider (admin only)"""
        p = TrackingProvider.query.filter_by(code=code).first_or_404()
        p.is_active = False
        db.session.commit()
        return {"msg": f"Provider {code} deactivated"}
