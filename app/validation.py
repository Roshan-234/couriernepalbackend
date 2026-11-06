"""Shared boundary validation for API requests."""
import math
from flask import request
from werkzeug.exceptions import BadRequest


def json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest('A JSON object is required')
    return data


def number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise BadRequest(f'{name} must be a finite number of at least {minimum}')
    return value


def text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise BadRequest(f'{name} must contain {1 if required else 0}–{limit} characters')
    return value.strip()


def pagination():
    try:
        page, size = int(request.args.get('page', 1)), int(request.args.get('per_page', 20))
    except ValueError:
        raise BadRequest('Page and per_page must be integers')
    if page < 1 or size < 1 or size > 100:
        raise BadRequest('Page must be positive; per_page must be between 1 and 100')
    return page, size


#: Sourced from the state machine so the vocabulary cannot drift. The legacy
#: seven are a subset, so existing clients keep working.
from app.shipment_status import STATUSES as SHIPMENT_STATUSES  # noqa: E402


def tracking_update(data):
    if data.get('status') not in SHIPMENT_STATUSES:
        raise BadRequest('Choose a valid shipment status')
    return {key: text(data.get(key, ''), key, limit) for key, limit in
            [('status', 50), ('location', 100), ('description', 1000)]}


def shipment_data(data):
    from app.models.service import Service
    from app.extensions import db
    for key, limit in [('origin', 255), ('destination', 255), ('origin_country', 100),
                       ('destination_country', 100), ('sender_name', 200), ('receiver_name', 200),
                       ('sender_email', 200), ('receiver_email', 200), ('sender_phone', 50),
                       ('receiver_phone', 50), ('sender_address', 2000), ('receiver_address', 2000),
                       ('notes', 5000), ('currency', 10), ('incoterms', 20)]:
        if key in data or key in ('origin', 'destination'):
            data[key] = text(data.get(key), key, limit, key in ('origin', 'destination'))
    number(data.get('total_weight_kg', 0), 'Weight')
    number(data.get('declared_value', 0), 'Declared value')
    packages = data.get('total_packages', 1)
    if type(packages) is not int or packages < 1:
        raise BadRequest('Package count must be a positive integer')
    if data.get('service_id') is not None:
        if type(data['service_id']) is not int:
            raise BadRequest('Service ID must be an integer')
        service = db.session.get(Service, data['service_id'])
        if not service or not service.is_active:
            raise BadRequest('Choose an active service')
    parcels = data.get('parcels', [])
    if not isinstance(parcels, list) or len(parcels) > 100:
        raise BadRequest('Provide at most 100 parcels')
    for parcel in parcels:
        if not isinstance(parcel, dict):
            raise BadRequest('Each parcel must be an object')
        if number(parcel.get('weight_kg'), 'Parcel weight') <= 0:
            raise BadRequest('Parcel weight must be positive')
        parcel['description'] = text(parcel.get('description', ''), 'Parcel description', 1000)
    return logistics_fields(data)


#: Writable logistics fields, with the check each one gets. Booking has to
#: accept these or a COD parcel cannot be created at all; everything priced
#: (freight, tax, totals) is deliberately absent, because the server computes
#: it and a client must not be able to set its own charge.
SERVICE_LEVELS = ('express', 'standard', 'economy', 'same_day')
PAYMENT_MODES = ('prepaid', 'cod', 'credit')


def logistics_fields(data):
    """Validate the operational fields added with the logistics modules."""
    from app.data.nepal_geo import district as find_district

    for key in ('origin_district', 'destination_district'):
        value = data.get(key)
        if value in (None, ''):
            data.pop(key, None)
            continue
        found = find_district(text(value, key, 60, required=True))
        if not found:
            raise BadRequest(f'{key} must be one of the 77 districts of Nepal')
        # Store the canonical name so rate lanes and reports group correctly.
        data[key] = found['name']

    if data.get('service_level') is not None:
        if data['service_level'] not in SERVICE_LEVELS:
            raise BadRequest(f"service_level must be one of {', '.join(SERVICE_LEVELS)}")

    if data.get('payment_mode') is not None:
        if data['payment_mode'] not in PAYMENT_MODES:
            raise BadRequest(f"payment_mode must be one of {', '.join(PAYMENT_MODES)}")

    for key in ('length_cm', 'width_cm', 'height_cm', 'insurance_amount', 'cod_amount'):
        if data.get(key) is not None:
            number(data[key], key)

    for key in ('is_cod', 'is_fragile', 'is_insured'):
        if key in data and not isinstance(data[key], bool):
            raise BadRequest(f'{key} must be true or false')

    for key, limit in (('reference_no', 100), ('contents_description', 2000),
                       ('cod_currency', 10)):
        if data.get(key) is not None:
            data[key] = text(data[key], key, limit)

    for key in ('origin_branch_id', 'destination_branch_id'):
        if data.get(key) is not None and type(data[key]) is not int:
            raise BadRequest(f'{key} must be an integer')

    # A COD parcel with no amount is a collection the rider cannot make.
    if data.get('is_cod') and not data.get('cod_amount'):
        raise BadRequest('A cash-on-delivery shipment needs a cod_amount')

    return data


def document_data(data, user_id, kind):
    """Validate financial inputs and ownership of linked business records."""
    from app.extensions import db
    from app.models.user import User
    from app.models.shipment import Shipment
    from app.models.packing_list import PackingList
    from werkzeug.exceptions import Forbidden
    user = db.session.get(User, user_id)
    for key, model, owner in [('shipment_id', Shipment, 'user_id'), ('packing_list_id', PackingList, 'created_by')]:
        if data.get(key) is not None:
            if type(data[key]) is not int:
                raise BadRequest(f'{key} must be an integer')
            related = db.session.get(model, data[key])
            if not related:
                raise BadRequest(f'{key} does not exist')
            if getattr(related, owner) != user_id and not user.is_admin:
                raise Forbidden('You cannot link another customer\'s documents')
    if 'status' in data:
        allowed = ('draft', 'issued', 'paid', 'cancelled') if kind == 'invoice' else ('draft', 'finalized')
        if data['status'] not in allowed:
            raise BadRequest('Invalid document status')
        if kind == 'invoice' and data['status'] in ('paid', 'issued') and not user.is_admin:
            raise Forbidden('Only administrators may issue or mark invoices paid')
    # fx_rate and total_npr come from the freight bill: the rate agreed on
    # the day, and the rupee figure the customer settles.
    for key in ('discount', 'tax_percentage', 'shipping_charge', 'total_packages',
                'fx_rate', 'total_npr'):
        if key in data:
            number(data[key], key)
    if data.get('tax_percentage', 0) > 100:
        raise BadRequest('Tax percentage cannot exceed 100')
    if 'items' in data:
        items = data['items']
        if not isinstance(items, list) or len(items) > 500:
            raise BadRequest('Provide at most 500 document items')
        for item in items:
            if not isinstance(item, dict):
                raise BadRequest('Each item must be an object')
            quantity = item.get('quantity', 0)
            if type(quantity) is not int or quantity < 0:
                raise BadRequest('Item quantity must be a non-negative integer')
            unit_key, total_key = ('unit_price', 'total_price') if kind == 'invoice' else ('unit_weight_kg', 'total_weight_kg')
            unit = number(item.get(unit_key, 0), unit_key)
            number(item.get(total_key, unit * quantity), total_key)
            # Derive totals consistently, rather than trusting client calculations.
            item[total_key] = round(unit * quantity, 2 if kind == 'invoice' else 3)
    for key, value in data.items():
        if key.endswith(('_name', '_address', '_country', '_phone', '_tin')) or key in ('notes', 'currency', 'payment_terms', 'incoterms'):
            data[key] = text(value, key, 5000 if key.endswith('_address') or key == 'notes' else 200)
    return data
