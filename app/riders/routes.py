"""Delivery riders and duty state"""
from datetime import datetime
from secrets import token_hex
from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.auth.utils import require_roles
from app.data import nepal_geo
from app.extensions import db
from app.models.branch import Branch
from app.models.rider import Rider
from app.models.shipment import Shipment
from app.models.user import User
from app.validation import json_object, number, pagination, text

rider_ns = Namespace('riders', description='Delivery riders and duty state')

rider_in = rider_ns.model('RiderIn', {
    'user_id':          fields.Integer(required=True, description='Existing user to turn into a rider'),
    'rider_code':       fields.String(description='Generated when omitted'),
    'branch_id':        fields.Integer(),
    'phone':            fields.String(),
    'national_id':      fields.String(),
    'license_number':   fields.String(),
    'emergency_contact_name':  fields.String(),
    'emergency_contact_phone': fields.String(),
    'vehicle_type':     fields.String(description=', '.join(Rider.VEHICLE_TYPES)),
    'vehicle_number':   fields.String(),
    'status':           fields.String(description=', '.join(Rider.STATUSES)),
    'serves_districts': fields.List(fields.String()),
    'max_daily_capacity': fields.Integer(),
    'handles_cod':      fields.Boolean(),
})

status_in = rider_ns.model('RiderStatusIn', {
    'status': fields.String(required=True, description=', '.join(Rider.STATUSES)),
})

location_in = rider_ns.model('RiderLocationIn', {
    'latitude':  fields.Float(required=True),
    'longitude': fields.Float(required=True),
})

_TEXT_FIELDS = {
    'phone': 50, 'national_id': 60, 'license_number': 60,
    'emergency_contact_name': 150, 'emergency_contact_phone': 50,
    'vehicle_number': 40,
}


def _current_rider():
    """The calling user's rider profile. /me routes are gated on this
    existing rather than on a role name — a rider is defined by the profile."""
    rider = Rider.query.filter_by(user_id=int(get_jwt_identity())).first()
    if not rider:
        rider_ns.abort(403, 'No rider profile for this account')
    return rider


def _clean_serves(value):
    if not isinstance(value, list) or len(value) > 77:
        rider_ns.abort(400, 'serves_districts must be a list of at most 77 districts')
    names = []
    for entry in value:
        found = nepal_geo.district(entry if isinstance(entry, str) else '')
        if not found:
            rider_ns.abort(400, f'Unknown district in serves_districts: {entry}')
        names.append(found['name'])
    return names


def _apply(rider, data):
    """Copy whitelisted fields onto a rider. Never mass-assign."""
    for key, limit in _TEXT_FIELDS.items():
        if key in data:
            setattr(rider, key, text(data[key], key, limit))
    if 'vehicle_type' in data:
        if data['vehicle_type'] not in Rider.VEHICLE_TYPES:
            rider_ns.abort(400, f"vehicle_type must be one of {', '.join(Rider.VEHICLE_TYPES)}")
        rider.vehicle_type = data['vehicle_type']
    if 'status' in data:
        if data['status'] not in Rider.STATUSES:
            rider_ns.abort(400, f"status must be one of {', '.join(Rider.STATUSES)}")
        rider.status = data['status']
    if 'branch_id' in data and data['branch_id'] is not None:
        branch = db.session.get(Branch, data['branch_id']) if isinstance(data['branch_id'], int) else None
        if not branch:
            rider_ns.abort(400, 'branch_id does not exist')
        rider.branch_id = branch.id
    if 'serves_districts' in data:
        rider.serves_districts = _clean_serves(data['serves_districts'])
    if 'max_daily_capacity' in data:
        rider.max_daily_capacity = int(number(data['max_daily_capacity'], 'max_daily_capacity', minimum=0))
    if 'handles_cod' in data:
        rider.handles_cod = bool(data['handles_cod'])
    return rider


def _generate_code():
    code = f"RD-{token_hex(3).upper()}"
    while Rider.query.filter_by(rider_code=code).first():
        code = f"RD-{token_hex(3).upper()}"
    return code


@rider_ns.route('/')
class RiderList(Resource):
    @rider_ns.doc('list_riders')
    @rider_ns.param('branch_id', 'Filter by home branch')
    @rider_ns.param('status', 'Filter by duty status')
    @rider_ns.param('district', 'Filter by a district the rider serves')
    @require_roles('admin', 'super_admin')
    def get(self):
        """List riders, paginated (admin)."""
        page, size = pagination()
        query = Rider.query
        if request.args.get('branch_id', '').isdigit():
            query = query.filter(Rider.branch_id == int(request.args['branch_id']))
        if request.args.get('status'):
            query = query.filter(Rider.status == request.args['status'])
        result = query.order_by(Rider.rider_code.asc()).paginate(page=page, per_page=size, error_out=False)
        items = result.items
        if request.args.get('district'):
            # serves_districts is JSON, so coverage is filtered in Python.
            found = nepal_geo.district(request.args['district'])
            name = found['name'] if found else request.args['district']
            items = [r for r in items if name in (r.serves_districts or [])]
        return {"items": [r.to_dict() for r in items], "total": result.total,
                "page": page, "pages": result.pages, "per_page": size}

    @rider_ns.doc('create_rider')
    @rider_ns.expect(rider_in)
    @rider_ns.response(201, 'Rider created')
    @rider_ns.response(409, 'User already has a rider profile')
    @require_roles('admin', 'super_admin')
    def post(self):
        """Create a rider profile for an existing user (admin)."""
        data = json_object()
        user_id = data.get('user_id')
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            rider_ns.abort(400, 'user_id must be an integer')
        if not db.session.get(User, user_id):
            rider_ns.abort(400, 'user_id does not exist')
        if Rider.query.filter_by(user_id=user_id).first():
            rider_ns.abort(409, 'User already has a rider profile')
        code = text(data['rider_code'], 'rider_code', 30, required=True).upper() if data.get('rider_code') else _generate_code()
        if Rider.query.filter_by(rider_code=code).first():
            rider_ns.abort(409, 'Rider code already exists')
        rider = Rider(user_id=user_id, rider_code=code)
        _apply(rider, data)
        db.session.add(rider)
        db.session.commit()
        return rider.to_dict(), 201


@rider_ns.route('/me')
class RiderMe(Resource):
    @rider_ns.doc('get_my_rider_profile')
    @rider_ns.response(403, 'No rider profile')
    @jwt_required()
    def get(self):
        """The calling user's own rider profile."""
        return _current_rider().to_dict()


@rider_ns.route('/me/status')
class RiderMeStatus(Resource):
    @rider_ns.doc('set_my_duty_status')
    @rider_ns.expect(status_in)
    @jwt_required()
    def patch(self):
        """Set the caller's duty status."""
        rider = _current_rider()
        data = json_object()
        if data.get('status') not in Rider.STATUSES:
            rider_ns.abort(400, f"status must be one of {', '.join(Rider.STATUSES)}")
        rider.status = data['status']
        rider.updated_at = datetime.utcnow()
        db.session.commit()
        return {"id": rider.id, "status": rider.status, "can_take_work": rider.can_take_work}


@rider_ns.route('/me/location')
class RiderMeLocation(Resource):
    @rider_ns.doc('report_my_location')
    @rider_ns.expect(location_in)
    @jwt_required()
    def post(self):
        """Report the caller's current position."""
        rider = _current_rider()
        data = json_object()
        for key, bound in (('latitude', 90), ('longitude', 180)):
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > bound:
                rider_ns.abort(400, f'{key} must be a number between -{bound} and {bound}')
        rider.latitude = float(data['latitude'])
        rider.longitude = float(data['longitude'])
        rider.location_updated_at = datetime.utcnow()
        db.session.commit()
        return {"id": rider.id, "latitude": rider.latitude, "longitude": rider.longitude,
                "location_updated_at": rider.location_updated_at.isoformat()}


@rider_ns.route('/me/shipments')
class RiderMeShipments(Resource):
    @rider_ns.doc('list_my_shipments')
    @jwt_required()
    def get(self):
        """Shipments currently assigned to the calling rider."""
        rider = _current_rider()
        page, size = pagination()
        query = Shipment.query.filter(Shipment.rider_id == rider.id)
        if request.args.get('status'):
            query = query.filter(Shipment.status == request.args['status'])
        result = query.order_by(Shipment.created_at.desc()).paginate(page=page, per_page=size, error_out=False)
        return {"items": [s.to_dict() for s in result.items], "total": result.total,
                "page": page, "pages": result.pages, "per_page": size}


@rider_ns.route('/<int:id>')
@rider_ns.response(404, 'Rider not found')
class RiderItem(Resource):
    @rider_ns.doc('get_rider')
    @require_roles('admin', 'super_admin')
    def get(self, id):
        """Fetch a rider (admin)."""
        rider = db.session.get(Rider, id)
        if not rider:
            rider_ns.abort(404, 'Rider not found')
        return rider.to_dict()

    @rider_ns.doc('update_rider')
    @rider_ns.expect(rider_in)
    @require_roles('admin', 'super_admin')
    def put(self, id):
        """Update a rider (admin). user_id and rider_code are immutable."""
        rider = db.session.get(Rider, id)
        if not rider:
            rider_ns.abort(404, 'Rider not found')
        _apply(rider, json_object())
        db.session.commit()
        return rider.to_dict()

    @rider_ns.doc('deactivate_rider')
    @require_roles('admin', 'super_admin')
    def delete(self, id):
        """Deactivate a rider (admin). History stays attached to the profile."""
        rider = db.session.get(Rider, id)
        if not rider:
            rider_ns.abort(404, 'Rider not found')
        rider.is_active = False
        rider.status = Rider.OFF_DUTY
        db.session.commit()
        return {"msg": "Rider deactivated", "id": rider.id, "is_active": rider.is_active}
