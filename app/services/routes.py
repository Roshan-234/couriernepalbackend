from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from app.auth.utils import require_roles
from app.models.service import Service
from app.models.user import User
from app.validation import json_object, text
from app.extensions import db

service_ns = Namespace('services', description='Service operations')

# Models
service_model = service_ns.model('Service', {
    'id': fields.Integer(readonly=True, description='Service ID'),
    'name': fields.String(required=True, description='Service name'),
    'code': fields.String(required=True, description='Service code'),
    'description': fields.String(required=True, description='Service description'),
    # Not a column. It is kept in the response so the admin UI keeps the key it
    # already reads, but it is ignored on write — see _IGNORED_FIELDS.
    'icon': fields.String(description='Service icon (not stored)'),
    'delivery_time': fields.String(description='Estimated delivery time'),
    'price_range': fields.String(description='Price range'),
    'is_active': fields.Boolean(default=True, description='Is active')
})

# ── Writable field whitelist ─────────────────────────────────────────────────
#
# ``Service(**payload)`` and a blind setattr loop let any caller name any
# attribute: 'id' to overwrite a primary key, 'shipments' to detach a
# relationship, or a typo to raise a TypeError and return 500. Every field a
# client may write is named here, with the bound it is checked against.

_STRING_FIELDS = {
    'name': 100, 'code': 30, 'description': 5000,
    'delivery_time': 50, 'price_range': 50,
}

_BOOL_FIELDS = ('is_active',)

_REQUIRED_FIELDS = ('name', 'code')

#: Server-owned, derived or display-only keys. A client that PUTs back a row it
#: just fetched sends these, so they are dropped rather than refused — but they
#: are never written.
_IGNORED_FIELDS = ('id', 'created_at', 'icon', 'shipments')


def _clean_service(data):
    """Validate a payload and return only the columns a client may write."""
    unknown = set(data) - set(_STRING_FIELDS) - set(_BOOL_FIELDS) - set(_IGNORED_FIELDS)
    if unknown:
        service_ns.abort(400, f"Unknown field(s): {', '.join(sorted(unknown))}")

    clean = {}
    for field, limit in _STRING_FIELDS.items():
        if field in data:
            value, required = data[field], field in _REQUIRED_FIELDS
            clean[field] = None if value is None and not required else text(value, field, limit, required)
    for field in _BOOL_FIELDS:
        if field in data:
            if not isinstance(data[field], bool):
                service_ns.abort(400, f"'{field}' must be true or false")
            clean[field] = data[field]
    return clean


def _caller_is_admin():
    """True when the request carries a valid admin token; anonymous is False."""
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
    except Exception:
        # These endpoints are public, so a missing or unusable token simply
        # means the caller is not an administrator.
        return False
    if identity is None:
        return False
    user = db.session.get(User, int(identity))
    return bool(user and user.is_admin)


def _visible_or_404(service):
    """A deactivated service is invisible to everyone but an administrator."""
    if service is None or (not service.is_active and not _caller_is_admin()):
        service_ns.abort(404, 'Service not found')
    return service


@service_ns.route('/')
class ServiceList(Resource):
    @service_ns.doc('list_services')
    @service_ns.marshal_list_with(service_model)
    def get(self):
        """List all services"""
        return Service.query.filter_by(is_active=True).all()

    @service_ns.doc('create_service')
    @service_ns.expect(service_model)
    @service_ns.marshal_with(service_model, code=201)
    @require_roles("admin", "super_admin")
    def post(self):
        """Create a new service"""
        data = _clean_service(json_object())
        for field in _REQUIRED_FIELDS:
            if not data.get(field):
                service_ns.abort(400, f"'{field}' is required")
        if Service.query.filter_by(code=data['code']).first():
            service_ns.abort(409, 'Service code already exists')
        service = Service(**data)
        db.session.add(service)
        db.session.commit()
        return service, 201


@service_ns.route('/<int:id>')
@service_ns.response(404, 'Service not found')
@service_ns.param('id', 'The service identifier')
class ServiceItem(Resource):
    @service_ns.doc('get_service')
    @service_ns.marshal_with(service_model)
    def get(self, id):
        """Fetch a service by ID"""
        return _visible_or_404(db.session.get(Service, id))

    @service_ns.doc('update_service')
    @service_ns.expect(service_model)
    @service_ns.marshal_with(service_model)
    @require_roles("admin", "super_admin")
    def put(self, id):
        """Update a service"""
        service = db.session.get(Service, id)
        if not service:
            service_ns.abort(404, 'Service not found')
        data = _clean_service(json_object())
        if 'code' in data and data['code'] != service.code:
            if Service.query.filter_by(code=data['code']).first():
                service_ns.abort(409, 'Service code already exists')
        for key, value in data.items():
            setattr(service, key, value)
        db.session.commit()
        return service

    @service_ns.doc('delete_service')
    @service_ns.response(204, 'Service deleted')
    @require_roles("admin", "super_admin")
    def delete(self, id):
        """Delete a service"""
        service = db.session.get(Service, id)
        if not service:
            service_ns.abort(404, 'Service not found')
        db.session.delete(service)
        db.session.commit()
        return '', 204
