from flask_restx import Namespace, Resource, fields
from app.auth.utils import require_roles
from app.models.warehouse import Warehouse
from app.validation import json_object, text
from app.extensions import db

warehouse_ns = Namespace('warehouses', description='Warehouse operations')

# Models
warehouse_model = warehouse_ns.model('Warehouse', {
    'id': fields.Integer(readonly=True, description='Warehouse ID'),
    'name': fields.String(required=True, description='Warehouse name'),
    'code': fields.String(required=True, description='Warehouse code'),
    'location': fields.String(description='Free-text location'),
    'address': fields.String(required=True, description='Address'),
    'city': fields.String(required=True, description='City'),
    'state': fields.String(required=True, description='State'),
    'country': fields.String(required=True, description='Country'),
    'postal_code': fields.String(required=True, description='Postal code'),
    'contact_person': fields.String(required=True, description='Contact person name'),
    'phone': fields.String(required=True, description='Contact phone'),
    'email': fields.String(required=True, description='Contact email'),
    'is_active': fields.Boolean(default=True, description='Is active')
})

# ── Writable field whitelist ─────────────────────────────────────────────────
#
# Filtering to "known columns" kept 'id' out but let any over-long or
# wrong-typed value through to the database. Each field is bounded here to the
# width of its column, so a 10 KB city name is a 400 rather than a driver error.

_STRING_FIELDS = {
    'name': 100, 'code': 20, 'location': 255, 'address': 255, 'city': 100,
    'state': 100, 'country': 100, 'postal_code': 20, 'contact_person': 120,
    'phone': 50, 'email': 120,
}

_BOOL_FIELDS = ('is_active',)

_REQUIRED_FIELDS = ('name', 'code')

#: Server-owned keys. Dropped rather than refused so a round-tripped row still
#: saves, but never written.
_IGNORED_FIELDS = ('id', 'created_at', 'parcels')


def _clean_warehouse(data):
    """Validate a payload and return only the columns a client may write."""
    unknown = set(data) - set(_STRING_FIELDS) - set(_BOOL_FIELDS) - set(_IGNORED_FIELDS)
    if unknown:
        warehouse_ns.abort(400, f"Unknown field(s): {', '.join(sorted(unknown))}")

    clean = {}
    for field, limit in _STRING_FIELDS.items():
        if field in data:
            value, required = data[field], field in _REQUIRED_FIELDS
            clean[field] = None if value is None and not required else text(value, field, limit, required)
    for field in _BOOL_FIELDS:
        if field in data:
            if not isinstance(data[field], bool):
                warehouse_ns.abort(400, f"'{field}' must be true or false")
            clean[field] = data[field]
    return clean


@warehouse_ns.route('/')
class WarehouseList(Resource):
    @warehouse_ns.doc('list_warehouses')
    @warehouse_ns.marshal_list_with(warehouse_model)
    def get(self):
        """List all warehouses"""
        return Warehouse.query.filter_by(is_active=True).all()

    @warehouse_ns.doc('create_warehouse')
    @warehouse_ns.expect(warehouse_model)
    @warehouse_ns.marshal_with(warehouse_model, code=201)
    @require_roles("admin", "super_admin")
    def post(self):
        """Create a new warehouse"""
        data = _clean_warehouse(json_object())
        for field in _REQUIRED_FIELDS:
            if not data.get(field):
                warehouse_ns.abort(400, f"'{field}' is required")
        if Warehouse.query.filter_by(code=data['code']).first():
            warehouse_ns.abort(409, "Warehouse code already exists")
        warehouse = Warehouse(**data)
        db.session.add(warehouse)
        db.session.commit()
        return warehouse, 201


@warehouse_ns.route('/<int:id>')
@warehouse_ns.response(404, 'Warehouse not found')
@warehouse_ns.param('id', 'The warehouse identifier')
class WarehouseItem(Resource):
    @warehouse_ns.doc('get_warehouse')
    @warehouse_ns.marshal_with(warehouse_model)
    def get(self, id):
        """Fetch a warehouse by ID"""
        warehouse = db.session.get(Warehouse, id)
        if not warehouse:
            warehouse_ns.abort(404, 'Warehouse not found')
        return warehouse

    @warehouse_ns.doc('update_warehouse')
    @warehouse_ns.expect(warehouse_model)
    @warehouse_ns.marshal_with(warehouse_model)
    @require_roles("admin", "super_admin")
    def put(self, id):
        """Update a warehouse"""
        warehouse = db.session.get(Warehouse, id)
        if not warehouse:
            warehouse_ns.abort(404, 'Warehouse not found')
        data = _clean_warehouse(json_object())
        if 'code' in data and data['code'] != warehouse.code:
            if Warehouse.query.filter_by(code=data['code']).first():
                warehouse_ns.abort(409, "Warehouse code already exists")
        for key, value in data.items():
            setattr(warehouse, key, value)
        db.session.commit()
        return warehouse

    @warehouse_ns.doc('delete_warehouse')
    @warehouse_ns.response(204, 'Warehouse deleted')
    @require_roles("admin", "super_admin")
    def delete(self, id):
        """Delete a warehouse"""
        warehouse = db.session.get(Warehouse, id)
        if not warehouse:
            warehouse_ns.abort(404, 'Warehouse not found')
        db.session.delete(warehouse)
        db.session.commit()
        return '', 204
