"""Rate cards, zones and quotes.

Endpoints:
  POST   /api/rates/quote                       Public quote (website calculator)
  GET    /api/rates/zones                       Public zone and district reference
  GET    /api/rates/cards                       List cards (admin, paginated)
  POST   /api/rates/cards                       Create a card (admin)
  GET    /api/rates/cards/<id>                  Card detail with entries (admin)
  PUT    /api/rates/cards/<id>                  Update a card (admin)
  DELETE /api/rates/cards/<id>                  Soft-delete a card (admin)
  POST   /api/rates/cards/<id>/entries          Add an entry (admin)
  POST   /api/rates/cards/<id>/entries/bulk     Load many entries at once (admin)
  PUT    /api/rates/entries/<id>                Update an entry (admin)
  DELETE /api/rates/entries/<id>                Remove an entry (admin)
  GET    /api/rates/surcharges                  List surcharges (admin)
  POST   /api/rates/surcharges                  Create a surcharge (admin)
  PUT    /api/rates/surcharges/<id>             Update a surcharge (admin)
"""
from datetime import datetime

from flask_restx import Namespace, Resource
from werkzeug.exceptions import BadRequest

from app import rating
from app.auth.utils import require_roles
from app.data.nepal_geo import DISTRICTS, ZONES, ZONE_CODES, district as geo_district
from app.extensions import db
from app.models.rate_card import RateCard, RateCardEntry, Surcharge
from app.validation import json_object, number, pagination, text

rate_ns = Namespace('rates', description='Rate cards, zones and quotes')

#: A bulk tariff load is one request; keep it bounded so a bad client cannot
#: push an unbounded insert through the session.
MAX_BULK_ENTRIES = 500

CARD_FIELDS = (
    'code', 'name', 'scope', 'service_level', 'currency', 'merchant_id',
    'is_default', 'is_active', 'effective_from', 'effective_to',
    'volumetric_divisor', 'min_chargeable_weight_kg', 'weight_step_kg',
    'fuel_surcharge_percent', 'vat_percent', 'cod_fee_percent', 'cod_fee_min',
    'insurance_percent', 'notes',
)

ENTRY_FIELDS = (
    'origin_zone', 'destination_zone', 'origin_district', 'destination_district',
    'destination_country', 'min_weight_kg', 'max_weight_kg', 'base_price',
    'price_per_kg', 'min_charge', 'transit_days_min', 'transit_days_max',
    'is_active',
)

SURCHARGE_FIELDS = (
    'code', 'name', 'description', 'applies_to', 'calculation', 'value',
    'min_amount', 'max_amount', 'zone', 'is_taxable', 'is_active', 'sort_order',
)


def _whitelist(data, allowed):
    """Reject unknown keys outright rather than silently dropping them."""
    unknown = set(data) - set(allowed)
    if unknown:
        raise BadRequest(f"Unknown or read-only field: {sorted(unknown)[0]}")
    return {key: value for key, value in data.items() if key in allowed}


def _boolean(value, name):
    if type(value) is not bool:
        raise BadRequest(f'{name} must be a boolean')
    return value


def _optional_int(value, name, minimum=0):
    if value is None:
        return None
    if type(value) is not int or isinstance(value, bool) or value < minimum:
        raise BadRequest(f'{name} must be an integer of at least {minimum}')
    return value


def _date(value, name):
    if value in (None, ''):
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        raise BadRequest(f'{name} must be an ISO date (YYYY-MM-DD)')


def _zone(value, name):
    if value in (None, ''):
        return None
    if value not in ZONE_CODES:
        raise BadRequest(f'{name} must be one of {", ".join(ZONE_CODES)}')
    return value


def _district_name(value, name):
    """Resolve a district slug or name to its canonical name, or None."""
    if value in (None, ''):
        return None
    found = geo_district(text(value, name, 60, True))
    if not found:
        raise BadRequest(f'{name} is not one of the 77 districts of Nepal')
    return found['name']


def clean_card(data, existing=None):
    """Validate a whitelisted rate card payload into column values."""
    values = _whitelist(data, CARD_FIELDS)
    if existing is None:
        for field in ('code', 'name'):
            if not values.get(field):
                raise BadRequest(f"'{field}' is required")
    merged = {
        'scope': RateCard.DOMESTIC, 'service_level': 'standard', 'currency': 'NPR',
        'merchant_id': None, 'is_default': False, 'is_active': True,
        'effective_from': None, 'effective_to': None, 'volumetric_divisor': 5000,
        'min_chargeable_weight_kg': 0.5, 'weight_step_kg': 0.5,
        'fuel_surcharge_percent': 0.0, 'vat_percent': 13.0, 'cod_fee_percent': 0.0,
        'cod_fee_min': 0.0, 'insurance_percent': 0.0, 'notes': '',
    }
    if existing is not None:
        merged = {key: getattr(existing, key) for key in CARD_FIELDS}
    merged.update(values)

    merged['code'] = text(merged.get('code'), 'code', 40, True)
    merged['name'] = text(merged.get('name'), 'name', 150, True)
    merged['currency'] = text(merged.get('currency') or 'NPR', 'currency', 10, True)
    merged['service_level'] = text(merged.get('service_level') or 'standard',
                                   'service_level', 30, True)
    merged['notes'] = text(merged.get('notes') or '', 'notes', 5000)
    if merged['scope'] not in RateCard.SCOPES:
        raise BadRequest(f'scope must be one of {", ".join(RateCard.SCOPES)}')
    merged['merchant_id'] = _optional_int(merged.get('merchant_id'), 'merchant_id', 1)
    for key in ('is_default', 'is_active'):
        merged[key] = _boolean(merged.get(key), key)
    for key in ('effective_from', 'effective_to'):
        value = merged.get(key)
        merged[key] = value if hasattr(value, 'isoformat') and not isinstance(value, str) \
            else _date(value, key)
    merged['volumetric_divisor'] = _optional_int(merged.get('volumetric_divisor'),
                                                 'volumetric_divisor', 1) or 5000
    for key in ('min_chargeable_weight_kg', 'weight_step_kg', 'fuel_surcharge_percent',
                'vat_percent', 'cod_fee_percent', 'cod_fee_min', 'insurance_percent'):
        merged[key] = float(number(merged.get(key) or 0.0, key))
    for key in ('fuel_surcharge_percent', 'vat_percent', 'cod_fee_percent',
                'insurance_percent'):
        if merged[key] > 100:
            raise BadRequest('Percentages cannot exceed 100')
    if merged['effective_to'] and merged['effective_from'] \
            and merged['effective_to'] < merged['effective_from']:
        raise BadRequest('effective_to must not precede effective_from')
    return merged


def clean_entry(data):
    """Validate a whitelisted rate card entry payload into column values."""
    values = _whitelist(data, ENTRY_FIELDS)
    merged = {
        'origin_zone': None, 'destination_zone': None, 'origin_district': None,
        'destination_district': None, 'destination_country': None,
        'min_weight_kg': 0.0, 'max_weight_kg': None, 'base_price': 0.0,
        'price_per_kg': 0.0, 'min_charge': 0.0, 'transit_days_min': None,
        'transit_days_max': None, 'is_active': True,
    }
    merged.update(values)

    merged['origin_zone'] = _zone(merged.get('origin_zone'), 'origin_zone')
    merged['destination_zone'] = _zone(merged.get('destination_zone'), 'destination_zone')
    merged['origin_district'] = _district_name(merged.get('origin_district'), 'origin_district')
    merged['destination_district'] = _district_name(merged.get('destination_district'),
                                                    'destination_district')
    if merged.get('destination_country'):
        merged['destination_country'] = text(merged['destination_country'],
                                             'destination_country', 100, True)
    else:
        merged['destination_country'] = None
    if not any(merged[key] for key in ('origin_zone', 'destination_zone', 'origin_district',
                                       'destination_district', 'destination_country')):
        raise BadRequest('An entry must pin down at least one lane field')

    merged['min_weight_kg'] = float(number(merged.get('min_weight_kg') or 0.0, 'min_weight_kg'))
    if merged.get('max_weight_kg') is not None:
        merged['max_weight_kg'] = float(number(merged['max_weight_kg'], 'max_weight_kg',
                                               merged['min_weight_kg']))
    for key in ('base_price', 'price_per_kg', 'min_charge'):
        merged[key] = float(number(merged.get(key) or 0.0, key))
    merged['transit_days_min'] = _optional_int(merged.get('transit_days_min'),
                                               'transit_days_min')
    merged['transit_days_max'] = _optional_int(merged.get('transit_days_max'),
                                               'transit_days_max',
                                               merged['transit_days_min'] or 0)
    merged['is_active'] = _boolean(merged.get('is_active'), 'is_active')
    return merged


def clean_surcharge(data, existing=None):
    """Validate a whitelisted surcharge payload into column values."""
    values = _whitelist(data, SURCHARGE_FIELDS)
    merged = {
        'code': None, 'name': None, 'description': '', 'applies_to': 'all',
        'calculation': Surcharge.FLAT, 'value': 0.0, 'min_amount': 0.0,
        'max_amount': None, 'zone': None, 'is_taxable': True, 'is_active': True,
        'sort_order': 0,
    }
    if existing is not None:
        merged = {key: getattr(existing, key) for key in SURCHARGE_FIELDS}
    merged.update(values)

    merged['code'] = text(merged.get('code'), 'code', 40, True)
    merged['name'] = text(merged.get('name'), 'name', 150, True)
    merged['description'] = text(merged.get('description') or '', 'description', 2000)
    if merged['applies_to'] not in Surcharge.APPLIES:
        raise BadRequest(f'applies_to must be one of {", ".join(Surcharge.APPLIES)}')
    if merged['calculation'] not in Surcharge.CALCULATIONS:
        raise BadRequest(f'calculation must be one of {", ".join(Surcharge.CALCULATIONS)}')
    merged['zone'] = _zone(merged.get('zone'), 'zone')
    for key in ('value', 'min_amount'):
        merged[key] = float(number(merged.get(key) or 0.0, key))
    if merged.get('max_amount') is not None:
        merged['max_amount'] = float(number(merged['max_amount'], 'max_amount',
                                            merged['min_amount']))
    if merged['calculation'] == Surcharge.PERCENT and merged['value'] > 100:
        raise BadRequest('Percentages cannot exceed 100')
    for key in ('is_taxable', 'is_active'):
        merged[key] = _boolean(merged.get(key), key)
    merged['sort_order'] = _optional_int(merged.get('sort_order') or 0, 'sort_order') or 0
    return merged


@rate_ns.route('/quote')
class RateQuote(Resource):
    @rate_ns.doc('quote_shipment')
    @rate_ns.response(200, 'Quote calculated')
    @rate_ns.response(400, 'Invalid quote request')
    @rate_ns.response(404, 'No rate available for this route')
    def post(self):
        """Quote a parcel. Public — the website calculator calls this."""
        data = json_object()
        weight = number(data.get('weight_kg', data.get('weight', 0)), 'Weight')
        if weight <= 0:
            raise BadRequest('Weight must be positive')

        country = data.get('destination_country')
        if country:
            country = text(country, 'destination_country', 100, True)
        destination_district = data.get('destination_district')
        if not destination_district and not country:
            raise BadRequest('Provide a destination district or a destination country')

        criteria = {
            'origin_district': _district_name(data.get('origin_district'), 'origin_district'),
            'destination_district': _district_name(destination_district,
                                                   'destination_district'),
            'destination_country': country,
            'weight_kg': weight,
            'service_level': text(data.get('service_level') or 'standard',
                                  'service_level', 30, True),
            'merchant_id': _optional_int(data.get('merchant_id'), 'merchant_id', 1),
            'declared_value': number(data.get('declared_value', 0) or 0, 'Declared value'),
            'cod_amount': number(data.get('cod_amount', 0) or 0, 'COD amount'),
        }
        for key in ('length_cm', 'width_cm', 'height_cm'):
            if data.get(key) is not None:
                criteria[key] = number(data[key], key)
        for key in ('is_cod', 'is_fragile', 'is_insured', 'is_oversize', 'is_weekend'):
            if key in data:
                criteria[key] = _boolean(data[key], key)

        result = rating.quote(**criteria)
        return result, 200 if result['status'] == rating.OK else 404


@rate_ns.route('/zones')
class RateZones(Resource):
    @rate_ns.doc('list_zones')
    @rate_ns.response(200, 'Zones and districts')
    def get(self):
        """Delivery zones and the districts in each. Public reference data."""
        return {
            'zones': [{'code': code, 'name': name, 'eta': eta} for code, name, eta in ZONES],
            'districts': list(DISTRICTS),
        }


@rate_ns.route('/cards')
class RateCardList(Resource):
    @rate_ns.doc('list_rate_cards', security='Bearer')
    @rate_ns.response(200, 'Paginated rate cards')
    @require_roles('admin', 'super_admin')
    def get(self):
        """List rate cards, newest first."""
        page, per_page = pagination()
        pag = RateCard.query.order_by(RateCard.id.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        return {
            'items': [card.to_dict() for card in pag.items],
            'total': pag.total,
            'page': pag.page,
            'pages': pag.pages,
            'per_page': per_page,
        }

    @rate_ns.doc('create_rate_card', security='Bearer')
    @rate_ns.response(201, 'Rate card created')
    @rate_ns.response(409, 'Rate card code already exists')
    @require_roles('admin', 'super_admin')
    def post(self):
        """Create a rate card."""
        values = clean_card(json_object())
        if RateCard.query.filter_by(code=values['code']).first():
            return {'message': 'Rate card code already exists'}, 409
        card = RateCard(**values)
        db.session.add(card)
        db.session.commit()
        return card.to_dict(), 201


@rate_ns.route('/cards/<int:id>')
@rate_ns.response(404, 'Rate card not found')
@rate_ns.param('id', 'The rate card identifier')
class RateCardItem(Resource):
    @rate_ns.doc('get_rate_card', security='Bearer')
    @require_roles('admin', 'super_admin')
    def get(self, id):
        """Fetch a rate card with its entries."""
        card = db.session.get(RateCard, id)
        if not card:
            return {'message': 'Rate card not found'}, 404
        return card.to_dict(include_entries=True)

    @rate_ns.doc('update_rate_card', security='Bearer')
    @require_roles('admin', 'super_admin')
    def put(self, id):
        """Update a rate card."""
        card = db.session.get(RateCard, id)
        if not card:
            return {'message': 'Rate card not found'}, 404
        values = clean_card(json_object(), card)
        clash = RateCard.query.filter(RateCard.code == values['code'],
                                      RateCard.id != card.id).first()
        if clash:
            return {'message': 'Rate card code already exists'}, 409
        for key, value in values.items():
            setattr(card, key, value)
        db.session.commit()
        return card.to_dict()

    @rate_ns.doc('deactivate_rate_card', security='Bearer')
    @rate_ns.response(200, 'Rate card deactivated')
    @require_roles('admin', 'super_admin')
    def delete(self, id):
        """Soft-delete a rate card. Historic quotes keep referencing it."""
        card = db.session.get(RateCard, id)
        if not card:
            return {'message': 'Rate card not found'}, 404
        card.is_active = False
        db.session.commit()
        return {'message': 'Rate card deactivated successfully'}


@rate_ns.route('/cards/<int:id>/entries')
@rate_ns.response(404, 'Rate card not found')
@rate_ns.param('id', 'The rate card identifier')
class RateCardEntryList(Resource):
    @rate_ns.doc('add_rate_entry', security='Bearer')
    @rate_ns.response(201, 'Entry added')
    @require_roles('admin', 'super_admin')
    def post(self, id):
        """Add one priced lane to a card."""
        card = db.session.get(RateCard, id)
        if not card:
            return {'message': 'Rate card not found'}, 404
        entry = RateCardEntry(rate_card_id=card.id, **clean_entry(json_object()))
        db.session.add(entry)
        db.session.commit()
        return entry.to_dict(), 201


@rate_ns.route('/cards/<int:id>/entries/bulk')
@rate_ns.response(404, 'Rate card not found')
@rate_ns.param('id', 'The rate card identifier')
class RateCardEntryBulk(Resource):
    @rate_ns.doc('bulk_load_rate_entries', security='Bearer')
    @rate_ns.response(201, 'Entries loaded')
    @require_roles('admin', 'super_admin')
    def post(self, id):
        """Load a whole tariff in one call.

        Every entry is validated before anything is written, so a typo in row
        300 does not leave a half-loaded tariff behind.
        """
        card = db.session.get(RateCard, id)
        if not card:
            return {'message': 'Rate card not found'}, 404
        payload = json_object()
        rows = payload.get('entries')
        if not isinstance(rows, list) or not rows:
            raise BadRequest('Provide a non-empty "entries" list')
        if len(rows) > MAX_BULK_ENTRIES:
            raise BadRequest(f'Provide at most {MAX_BULK_ENTRIES} entries per request')
        if payload.get('replace') is not None:
            _boolean(payload['replace'], 'replace')

        cleaned = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise BadRequest(f'Entry {index + 1} must be an object')
            try:
                cleaned.append(clean_entry(row))
            except BadRequest as exc:
                raise BadRequest(f'Entry {index + 1}: {exc.description}')

        if payload.get('replace'):
            RateCardEntry.query.filter_by(rate_card_id=card.id).delete()
        created = [RateCardEntry(rate_card_id=card.id, **values) for values in cleaned]
        db.session.add_all(created)
        db.session.commit()
        return {'created': len(created),
                'entries': [entry.to_dict() for entry in created]}, 201


@rate_ns.route('/entries/<int:id>')
@rate_ns.response(404, 'Entry not found')
@rate_ns.param('id', 'The rate card entry identifier')
class RateEntryItem(Resource):
    @rate_ns.doc('update_rate_entry', security='Bearer')
    @require_roles('admin', 'super_admin')
    def put(self, id):
        """Replace a lane's pricing and coverage."""
        entry = db.session.get(RateCardEntry, id)
        if not entry:
            return {'message': 'Entry not found'}, 404
        for key, value in clean_entry(json_object()).items():
            setattr(entry, key, value)
        db.session.commit()
        return entry.to_dict()

    @rate_ns.doc('delete_rate_entry', security='Bearer')
    @rate_ns.response(200, 'Entry removed')
    @require_roles('admin', 'super_admin')
    def delete(self, id):
        """Remove a lane from its card."""
        entry = db.session.get(RateCardEntry, id)
        if not entry:
            return {'message': 'Entry not found'}, 404
        db.session.delete(entry)
        db.session.commit()
        return {'message': 'Entry removed successfully'}


@rate_ns.route('/surcharges')
class SurchargeList(Resource):
    @rate_ns.doc('list_surcharges', security='Bearer')
    @require_roles('admin', 'super_admin')
    def get(self):
        """List every surcharge, in application order."""
        rows = Surcharge.query.order_by(Surcharge.sort_order.asc(),
                                        Surcharge.id.asc()).all()
        return {'items': [row.to_dict() for row in rows], 'total': len(rows)}

    @rate_ns.doc('create_surcharge', security='Bearer')
    @rate_ns.response(201, 'Surcharge created')
    @rate_ns.response(409, 'Surcharge code already exists')
    @require_roles('admin', 'super_admin')
    def post(self):
        """Create a surcharge."""
        values = clean_surcharge(json_object())
        if Surcharge.query.filter_by(code=values['code']).first():
            return {'message': 'Surcharge code already exists'}, 409
        surcharge = Surcharge(**values)
        db.session.add(surcharge)
        db.session.commit()
        return surcharge.to_dict(), 201


@rate_ns.route('/surcharges/<int:id>')
@rate_ns.response(404, 'Surcharge not found')
@rate_ns.param('id', 'The surcharge identifier')
class SurchargeItem(Resource):
    @rate_ns.doc('update_surcharge', security='Bearer')
    @require_roles('admin', 'super_admin')
    def put(self, id):
        """Update a surcharge."""
        surcharge = db.session.get(Surcharge, id)
        if not surcharge:
            return {'message': 'Surcharge not found'}, 404
        values = clean_surcharge(json_object(), surcharge)
        clash = Surcharge.query.filter(Surcharge.code == values['code'],
                                       Surcharge.id != surcharge.id).first()
        if clash:
            return {'message': 'Surcharge code already exists'}, 409
        for key, value in values.items():
            setattr(surcharge, key, value)
        db.session.commit()
        return surcharge.to_dict()
