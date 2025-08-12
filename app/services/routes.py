from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.service import Service
from app.extensions import db

service_ns = Namespace('services', description='Service operations')

# Models
service_model = service_ns.model('Service', {
    'id': fields.Integer(readonly=True, description='Service ID'),
    'name': fields.String(required=True, description='Service name'),
    'code': fields.String(required=True, description='Service code'),
    'description': fields.String(required=True, description='Service description'),
    'icon': fields.String(description='Service icon'),
    'delivery_time': fields.String(description='Estimated delivery time'),
    'price_range': fields.String(description='Price range'),
    'is_active': fields.Boolean(default=True, description='Is active')
})

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
    @jwt_required()
    def post(self):
        """Create a new service"""
        data = service_ns.payload
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
        return Service.query.get_or_404(id)

    @service_ns.doc('update_service')
    @service_ns.expect(service_model)
    @service_ns.marshal_with(service_model)
    @jwt_required()
    def put(self, id):
        """Update a service"""
        service = Service.query.get_or_404(id)
        data = service_ns.payload
        for key, value in data.items():
            setattr(service, key, value)
        db.session.commit()
        return service

    @service_ns.doc('delete_service')
    @service_ns.response(204, 'Service deleted')
    @jwt_required()
    def delete(self, id):
        """Delete a service"""
        service = Service.query.get_or_404(id)
        db.session.delete(service)
        db.session.commit()
        return '', 204
