from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.pricing import PricingRule
from app.extensions import db
from app.auth.utils import require_roles

pricing_ns = Namespace('pricing', description='Pricing operations')

# Models
pricing_model = pricing_ns.model('PricingRule', {
    'id': fields.Integer(readonly=True, description='Pricing rule ID'),
    'service_type': fields.String(required=True, description='Service type'),
    'shipment_type': fields.String(required=True, description='Shipment type'),
    'origin_country': fields.String(required=True, description='Origin country'),
    'destination_country': fields.String(required=True, description='Destination country'),
    'base_price': fields.Float(required=True, description='Base price'),
    'price_per_kg': fields.Float(required=True, description='Price per kg'),
    'min_weight': fields.Float(required=True, description='Minimum weight'),
    'max_weight': fields.Float(description='Maximum weight'),
    'insurance_percentage': fields.Float(description='Insurance percentage'),
    'tax_percentage': fields.Float(description='Tax percentage'),
    'is_active': fields.Boolean(default=True, description='Is active')
})

price_calculation_model = pricing_ns.model('PriceCalculation', {
    'service_type': fields.String(required=True, description='Service type'),
    'shipment_type': fields.String(required=True, description='Shipment type'),
    'origin_country': fields.String(required=True, description='Origin country'),
    'destination_country': fields.String(required=True, description='Destination country'),
    'weight': fields.Float(required=True, description='Shipment weight'),
    'declared_value': fields.Float(description='Declared value for insurance')
})


@pricing_ns.route('/')
class PricingList(Resource):
    @pricing_ns.doc('list_pricing_rules')
    @pricing_ns.marshal_list_with(pricing_model)
    def get(self):
        """List all pricing rules"""
        return PricingRule.query.filter_by(is_active=True).all()

    @pricing_ns.doc('create_pricing_rule')
    @pricing_ns.expect(pricing_model)
    @pricing_ns.marshal_with(pricing_model, code=201)
    @jwt_required()
    def post(self):
        """Create a new pricing rule"""
        data = pricing_ns.payload
        rule = PricingRule(**data)
        db.session.add(rule)
        db.session.commit()
        return rule, 201


@pricing_ns.route('/<int:id>')
@pricing_ns.response(404, 'Pricing rule not found')
@pricing_ns.param('id', 'The pricing rule identifier')
class PricingItem(Resource):
    @pricing_ns.doc('get_pricing_rule')
    @pricing_ns.marshal_with(pricing_model)
    def get(self, id):
        """Fetch a pricing rule by ID"""
        return PricingRule.query.get_or_404(id)

    @pricing_ns.doc('update_pricing_rule')
    @pricing_ns.expect(pricing_model)
    @pricing_ns.marshal_with(pricing_model)
    @jwt_required()
    def put(self, id):
        """Update a pricing rule"""
        rule = PricingRule.query.get_or_404(id)
        data = pricing_ns.payload
        for key, value in data.items():
            setattr(rule, key, value)
        db.session.commit()
        return rule

    @pricing_ns.doc('delete_pricing_rule')
    @pricing_ns.response(204, 'Pricing rule deleted')
    @jwt_required()
    def delete(self, id):
        """Delete a pricing rule"""
        rule = PricingRule.query.get_or_404(id)
        db.session.delete(rule)
        db.session.commit()
        return '', 204


@pricing_ns.route('/calculate')
class PricingCalculator(Resource):
    @pricing_ns.doc('calculate_price')
    @pricing_ns.expect(price_calculation_model)
    def post(self):
        """Calculate shipping price"""
        data = pricing_ns.payload
        try:
            rule = PricingRule.query.filter_by(
                service_type=data['service_type'],
                shipment_type=data['shipment_type'],
                origin_country=data['origin_country'],
                destination_country=data['destination_country'],
                is_active=True
            ).first_or_404()
            
            return rule.calculate_price(
                data['weight'],
                data.get('declared_value', 0)
            )
        except ValueError as e:
            return {"message": str(e)}, 400


@pricing_ns.route("/rules")
class PricingRules(Resource):
    @jwt_required()
    @require_roles("super_admin", "admin")
    def get(self):
        """Get all pricing rules (Admin only)"""
        rules = PricingRule.query.filter_by(is_active=True).all()
        return {"rules": [rule.to_dict() for rule in rules]}

    @jwt_required()
    @require_roles("super_admin", "admin")
    def post(self):
        """Create a new pricing rule (Admin only)"""
        data = request.get_json()
        rule = PricingRule(
            service_type=data['service_type'],
            shipment_type=data['shipment_type'],
            origin_country=data['origin_country'],
            destination_country=data['destination_country'],
            base_price=data['base_price'],
            price_per_kg=data['price_per_kg'],
            min_weight=data.get('min_weight', 0),
            max_weight=data.get('max_weight'),
            insurance_percentage=data.get('insurance_percentage', 0),
            tax_percentage=data.get('tax_percentage', 0)
        )
        db.session.add(rule)
        db.session.commit()
        return rule.to_dict(), 201


@pricing_ns.route("/rules/<int:id>")
class PricingRuleDetail(Resource):
    @jwt_required()
    @require_roles("super_admin", "admin")
    def get(self, id):
        """Get pricing rule details (Admin only)"""
        rule = PricingRule.query.get_or_404(id)
        return rule.to_dict()

    @jwt_required()
    @require_roles("super_admin", "admin")
    def put(self, id):
        """Update pricing rule (Admin only)"""
        rule = PricingRule.query.get_or_404(id)
        data = request.get_json()
        for key, value in data.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
        db.session.commit()
        return rule.to_dict()

    @jwt_required()
    @require_roles("super_admin", "admin")
    def delete(self, id):
        """Delete pricing rule (Admin only)"""
        rule = PricingRule.query.get_or_404(id)
        rule.is_active = False
        db.session.commit()
        return {"message": "Rule deactivated successfully"}
