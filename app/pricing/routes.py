from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.pricing import PricingRule
from app.extensions import db
from app.auth.utils import require_roles
import math

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


from app.validation import json_object, number, text
from sqlalchemy import or_
from werkzeug.exceptions import BadRequest


def clean_rule(data, existing=None):
    allowed = set(pricing_model) - {'id'}
    if set(data) - allowed:
        raise BadRequest('Unknown or read-only pricing field')
    values = existing.to_dict() if existing else {'min_weight': 0, 'max_weight': None,
        'insurance_percentage': 0, 'tax_percentage': 0, 'is_active': True}
    values.update(data)
    values.pop('id', None)
    for key, limit in [('service_type', 50), ('shipment_type', 50), ('origin_country', 100), ('destination_country', 100)]:
        values[key] = text(values.get(key), key, limit, True)
    for key in ['base_price', 'price_per_kg', 'min_weight', 'insurance_percentage', 'tax_percentage']:
        number(values.get(key), key)
    if values['max_weight'] is not None:
        number(values['max_weight'], 'max_weight', values['min_weight'])
    if any(values[key] > 100 for key in ['insurance_percentage', 'tax_percentage']):
        raise BadRequest('Percentages cannot exceed 100')
    if type(values['is_active']) is not bool:
        raise BadRequest('is_active must be a boolean')
    return values


def save_rule(existing=None):
    values = clean_rule(json_object(), existing)
    rule = existing or PricingRule()
    for key, value in values.items():
        setattr(rule, key, value)
    db.session.add(rule)
    db.session.commit()
    return rule.to_dict(), 200 if existing else 201


@pricing_ns.route('/')
class PricingList(Resource):
    def get(self):
        return [r.to_dict() for r in PricingRule.query.filter_by(is_active=True).all()]

    @require_roles('admin', 'super_admin')
    def post(self):
        return save_rule()


@pricing_ns.route('/<int:id>')
@pricing_ns.route('/rules/<int:id>')
class PricingItem(Resource):
    @require_roles('admin', 'super_admin')
    def get(self, id):
        return PricingRule.query.get_or_404(id).to_dict()

    @require_roles('admin', 'super_admin')
    def put(self, id):
        return save_rule(PricingRule.query.get_or_404(id))

    @require_roles('admin', 'super_admin')
    def delete(self, id):
        rule = PricingRule.query.get_or_404(id)
        rule.is_active = False
        db.session.commit()
        return {'message': 'Rule deactivated successfully'}


@pricing_ns.route('/rules')
class PricingRules(Resource):
    @require_roles('admin', 'super_admin')
    def get(self):
        return {'rules': [r.to_dict() for r in PricingRule.query.order_by(PricingRule.id).all()]}

    @require_roles('admin', 'super_admin')
    def post(self):
        return save_rule()


@pricing_ns.route('/calculate')
class PricingCalculator(Resource):
    @pricing_ns.expect(price_calculation_model, validate=True)
    def post(self):
        data = json_object()
        weight = number(data.get('weight'), 'Weight')
        if weight <= 0:
            raise BadRequest('Weight must be positive')
        declared = number(data.get('declared_value', 0), 'Declared value')
        route = {key: text(data.get(key), key, 100, True) for key in
                 ['service_type', 'shipment_type', 'origin_country', 'destination_country']}
        rule = PricingRule.query.filter_by(**route, is_active=True).filter(
            PricingRule.min_weight <= weight,
            or_(PricingRule.max_weight.is_(None), PricingRule.max_weight >= weight)
        ).order_by(PricingRule.min_weight.desc(), PricingRule.id.asc()).first()
        if not rule:
            return {'message': 'No rate is configured for this route and weight. Contact us for a quote.'}, 404
        return rule.calculate_price(weight, declared)
