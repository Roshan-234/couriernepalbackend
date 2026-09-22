from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.warehouse import Warehouse
from app.extensions import db

warehouse_ns = Namespace('warehouses', description='Warehouse operations')

# Models
warehouse_model = warehouse_ns.model('Warehouse', {
    'id': fields.Integer(readonly=True, description='Warehouse ID'),
    'name': fields.String(required=True, description='Warehouse name'),
    'code': fields.String(required=True, description='Warehouse code'),
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
    @jwt_required()
    def post(self):
        """Create a new warehouse"""
        data = warehouse_ns.payload or {}
        # Required business keys
        for field in ("name", "code"):
            if not data.get(field):
                warehouse_ns.abort(400, f"'{field}' is required")
        # Only accept known columns to avoid TypeErrors on unexpected keys
        allowed = {c.name for c in Warehouse.__table__.columns} - {"id", "created_at"}
        clean = {k: v for k, v in data.items() if k in allowed}
        if Warehouse.query.filter_by(code=clean["code"]).first():
            warehouse_ns.abort(409, "Warehouse code already exists")
        warehouse = Warehouse(**clean)
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
        return Warehouse.query.get_or_404(id)

    @warehouse_ns.doc('update_warehouse')
    @warehouse_ns.expect(warehouse_model)
    @warehouse_ns.marshal_with(warehouse_model)
    @jwt_required()
    def put(self, id):
        """Update a warehouse"""
        warehouse = Warehouse.query.get_or_404(id)
        data = warehouse_ns.payload or {}
        allowed = {c.name for c in Warehouse.__table__.columns} - {"id", "created_at"}
        for key, value in data.items():
            if key in allowed:
                setattr(warehouse, key, value)
        db.session.commit()
        return warehouse

    @warehouse_ns.doc('delete_warehouse')
    @warehouse_ns.response(204, 'Warehouse deleted')
    @jwt_required()
    def delete(self, id):
        """Delete a warehouse"""
        warehouse = Warehouse.query.get_or_404(id)
        db.session.delete(warehouse)
        db.session.commit()
        return '', 204
