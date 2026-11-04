"""Pickup requests and assignment"""
from datetime import date, datetime
from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.auth.utils import require_roles
from app.data import nepal_geo
from app.extensions import db
from app.notifications.dispatch import notify
from app.models.branch import Branch
from app.models.pickup import Pickup
from app.models.rider import Rider
from app.models.user import User
from app.validation import json_object, number, pagination, text

pickup_ns = Namespace('pickups', description='Pickup requests and assignment')

pickup_in = pickup_ns.model('PickupIn', {
    'contact_name':   fields.String(required=True),
    'contact_phone':  fields.String(required=True),
    'address':        fields.String(required=True),
    'district':       fields.String(required=True),
    'city':           fields.String(),
    'landmark':       fields.String(),
    'latitude':       fields.Float(),
    'longitude':      fields.Float(),
    'scheduled_date': fields.String(description='YYYY-MM-DD'),
    'time_slot':      fields.String(description=', '.join(Pickup.SLOTS)),
    'package_count':  fields.Integer(),
    'estimated_weight_kg': fields.Float(),
    'notes':          fields.String(),
    'branch_id':      fields.Integer(),
})

assign_in = pickup_ns.model('PickupAssignIn', {
    'rider_id': fields.Integer(required=True),
})

reschedule_in = pickup_ns.model('PickupRescheduleIn', {
    'scheduled_date': fields.String(required=True, description='YYYY-MM-DD'),
    'time_slot':      fields.String(),
})

complete_in = pickup_ns.model('PickupCompleteIn', {
    'collected_count': fields.Integer(required=True),
})

_TEXT_FIELDS = {
    'contact_name': 150, 'contact_phone': 50, 'address': 2000,
    'city': 100, 'landmark': 255, 'notes': 5000,
}


def _caller():
    user = db.session.get(User, int(get_jwt_identity()))
    if not user:
        pickup_ns.abort(404, 'User not found')
    return user


def _rider_profile(user):
    return Rider.query.filter_by(user_id=user.id).first()


def _parse_date(value, name='scheduled_date'):
    if value is None:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        pickup_ns.abort(400, f'{name} must be an ISO date (YYYY-MM-DD)')


def _apply(pickup, data, creating=False):
    """Copy whitelisted fields onto a pickup. Never mass-assign."""
    for key, limit in _TEXT_FIELDS.items():
        required = key in ('contact_name', 'contact_phone', 'address')
        if key in data or (creating and required):
            setattr(pickup, key, text(data.get(key), key, limit, required=required and creating))
    if 'district' in data or creating:
        found = nepal_geo.district(data.get('district') if isinstance(data.get('district'), str) else '')
        if not found:
            pickup_ns.abort(400, 'Unknown district')
        pickup.district = found['name']
    if 'time_slot' in data:
        if data['time_slot'] not in Pickup.SLOTS:
            pickup_ns.abort(400, f"time_slot must be one of {', '.join(Pickup.SLOTS)}")
        pickup.time_slot = data['time_slot']
    if 'scheduled_date' in data:
        pickup.scheduled_date = _parse_date(data['scheduled_date'])
    if 'package_count' in data:
        pickup.package_count = int(number(data['package_count'], 'package_count', minimum=1))
    if 'estimated_weight_kg' in data:
        pickup.estimated_weight_kg = float(number(data['estimated_weight_kg'], 'estimated_weight_kg'))
    for key, bound in (('latitude', 90), ('longitude', 180)):
        if data.get(key) is not None:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > bound:
                pickup_ns.abort(400, f'{key} must be a number between -{bound} and {bound}')
            setattr(pickup, key, float(value))
    if 'branch_id' in data and data['branch_id'] is not None:
        branch = db.session.get(Branch, data['branch_id']) if isinstance(data['branch_id'], int) else None
        if not branch:
            pickup_ns.abort(400, 'branch_id does not exist')
        pickup.branch_id = branch.id
    return pickup


def _get_owned(id, user):
    """A customer may only reach their own pickups; admins reach any."""
    pickup = db.session.get(Pickup, id)
    if not pickup:
        pickup_ns.abort(404, 'Pickup not found')
    if user.is_admin:
        return pickup
    if pickup.requested_by == user.id or pickup.merchant_id == user.id:
        return pickup
    rider = _rider_profile(user)
    if rider and pickup.rider_id == rider.id:
        return pickup
    pickup_ns.abort(403, 'Access denied')


@pickup_ns.route('/')
class PickupList(Resource):
    @pickup_ns.doc('list_all_pickups')
    @pickup_ns.param('status', 'Filter by pickup status')
    @pickup_ns.param('date', 'Filter by scheduled date (YYYY-MM-DD)')
    @pickup_ns.param('branch_id', 'Filter by branch')
    @pickup_ns.param('rider_id', 'Filter by assigned rider')
    @require_roles('admin', 'super_admin')
    def get(self):
        """List every pickup, paginated (admin)."""
        page, size = pagination()
        query = Pickup.query
        if request.args.get('status'):
            query = query.filter(Pickup.status == request.args['status'])
        if request.args.get('date'):
            query = query.filter(Pickup.scheduled_date == _parse_date(request.args['date'], 'date'))
        if request.args.get('branch_id', '').isdigit():
            query = query.filter(Pickup.branch_id == int(request.args['branch_id']))
        if request.args.get('rider_id', '').isdigit():
            query = query.filter(Pickup.rider_id == int(request.args['rider_id']))
        result = query.order_by(Pickup.created_at.desc()).paginate(page=page, per_page=size, error_out=False)
        return {"items": [p.to_dict() for p in result.items], "total": result.total,
                "page": page, "pages": result.pages, "per_page": size}

    @pickup_ns.doc('create_pickup')
    @pickup_ns.expect(pickup_in)
    @pickup_ns.response(201, 'Pickup requested')
    @jwt_required()
    def post(self):
        """Request a pickup. The caller owns the request."""
        user = _caller()
        data = json_object()
        pickup = Pickup(pickup_no=Pickup.generate_number(), requested_by=user.id,
                        merchant_id=user.id, status=Pickup.REQUESTED)
        _apply(pickup, data, creating=True)
        if pickup.scheduled_date and pickup.scheduled_date < date.today():
            pickup_ns.abort(400, 'scheduled_date cannot be in the past')
        db.session.add(pickup)
        db.session.commit()
        return pickup.to_dict(), 201


@pickup_ns.route('/my')
class MyPickups(Resource):
    @pickup_ns.doc('list_my_pickups')
    @jwt_required()
    def get(self):
        """The caller's own pickup requests."""
        user = _caller()
        page, size = pagination()
        query = Pickup.query.filter(Pickup.requested_by == user.id)
        if request.args.get('status'):
            query = query.filter(Pickup.status == request.args['status'])
        result = query.order_by(Pickup.created_at.desc()).paginate(page=page, per_page=size, error_out=False)
        return {"items": [p.to_dict() for p in result.items], "total": result.total,
                "page": page, "pages": result.pages, "per_page": size}


@pickup_ns.route('/assigned')
class AssignedPickups(Resource):
    @pickup_ns.doc('list_assigned_pickups')
    @jwt_required()
    def get(self):
        """The calling rider's own pickup queue."""
        rider = _rider_profile(_caller())
        if not rider:
            pickup_ns.abort(403, 'No rider profile for this account')
        open_states = (Pickup.ASSIGNED, Pickup.SCHEDULED, Pickup.IN_PROGRESS)
        query = Pickup.query.filter(Pickup.rider_id == rider.id)
        if request.args.get('status'):
            query = query.filter(Pickup.status == request.args['status'])
        else:
            query = query.filter(Pickup.status.in_(open_states))
        pickups = query.order_by(Pickup.scheduled_date.asc(), Pickup.created_at.asc()).all()
        return {"items": [p.to_dict() for p in pickups], "total": len(pickups)}


@pickup_ns.route('/<int:id>')
@pickup_ns.response(404, 'Pickup not found')
class PickupItem(Resource):
    @pickup_ns.doc('get_pickup')
    @jwt_required()
    def get(self, id):
        """Fetch a pickup (owner, assigned rider or admin)."""
        return _get_owned(id, _caller()).to_dict()


@pickup_ns.route('/<int:id>/cancel')
class PickupCancel(Resource):
    @pickup_ns.doc('cancel_pickup')
    @pickup_ns.response(409, 'Pickup can no longer be cancelled')
    @jwt_required()
    def post(self, id):
        """Cancel a pickup (owner or admin)."""
        user = _caller()
        pickup = _get_owned(id, user)
        if not (user.is_admin or pickup.requested_by == user.id or pickup.merchant_id == user.id):
            pickup_ns.abort(403, 'Only the requester may cancel this pickup')
        if pickup.status in (Pickup.COMPLETED, Pickup.CANCELLED, Pickup.FAILED):
            pickup_ns.abort(409, f'A {pickup.status} pickup cannot be cancelled')
        pickup.status = Pickup.CANCELLED
        db.session.commit()
        return pickup.to_dict()


@pickup_ns.route('/<int:id>/assign')
class PickupAssign(Resource):
    @pickup_ns.doc('assign_pickup')
    @pickup_ns.expect(assign_in)
    @require_roles('admin', 'super_admin')
    def post(self, id):
        """Assign a rider to a pickup (admin)."""
        pickup = db.session.get(Pickup, id)
        if not pickup:
            pickup_ns.abort(404, 'Pickup not found')
        if pickup.status in (Pickup.COMPLETED, Pickup.CANCELLED):
            pickup_ns.abort(409, f'A {pickup.status} pickup cannot be assigned')
        data = json_object()
        rider = db.session.get(Rider, data['rider_id']) if isinstance(data.get('rider_id'), int) else None
        if not rider:
            pickup_ns.abort(400, 'rider_id does not exist')
        if not rider.can_take_work:
            pickup_ns.abort(409, 'Rider is not available for work')
        pickup.rider_id = rider.id
        pickup.status = Pickup.ASSIGNED
        pickup.assigned_at = datetime.utcnow()
        db.session.commit()
        return pickup.to_dict()


@pickup_ns.route('/<int:id>/reschedule')
class PickupReschedule(Resource):
    @pickup_ns.doc('reschedule_pickup')
    @pickup_ns.expect(reschedule_in)
    @require_roles('admin', 'super_admin')
    def post(self, id):
        """Move a pickup to another date or slot (admin)."""
        pickup = db.session.get(Pickup, id)
        if not pickup:
            pickup_ns.abort(404, 'Pickup not found')
        if pickup.status in (Pickup.COMPLETED, Pickup.CANCELLED):
            pickup_ns.abort(409, f'A {pickup.status} pickup cannot be rescheduled')
        data = json_object()
        scheduled = _parse_date(data.get('scheduled_date'))
        if not scheduled:
            pickup_ns.abort(400, 'scheduled_date is required')
        if scheduled < date.today():
            pickup_ns.abort(400, 'scheduled_date cannot be in the past')
        pickup.scheduled_date = scheduled
        if data.get('time_slot'):
            if data['time_slot'] not in Pickup.SLOTS:
                pickup_ns.abort(400, f"time_slot must be one of {', '.join(Pickup.SLOTS)}")
            pickup.time_slot = data['time_slot']
        pickup.status = Pickup.ASSIGNED if pickup.rider_id else Pickup.SCHEDULED
        db.session.commit()
        return pickup.to_dict()


@pickup_ns.route('/<int:id>/complete')
class PickupComplete(Resource):
    @pickup_ns.doc('complete_pickup')
    @pickup_ns.expect(complete_in)
    @pickup_ns.response(409, 'Pickup already closed')
    @jwt_required()
    def post(self, id):
        """Mark a pickup collected (assigned rider or admin)."""
        user = _caller()
        pickup = db.session.get(Pickup, id)
        if not pickup:
            pickup_ns.abort(404, 'Pickup not found')
        rider = _rider_profile(user)
        if not user.is_admin and not (rider and pickup.rider_id == rider.id):
            pickup_ns.abort(403, 'Only the assigned rider may complete this pickup')
        if pickup.status in (Pickup.COMPLETED, Pickup.CANCELLED):
            pickup_ns.abort(409, f'A {pickup.status} pickup cannot be completed')
        data = json_object()
        collected = data.get('collected_count', pickup.package_count or 0)
        pickup.collected_count = int(number(collected, 'collected_count', minimum=0))
        pickup.status = Pickup.COMPLETED
        pickup.completed_at = datetime.utcnow()
        if not pickup.started_at:
            pickup.started_at = pickup.completed_at
        db.session.commit()
        # After the commit, and to the pickup's own contact rather than the
        # account that booked it: on a merchant pickup those are often
        # different people, and the one at the door is the one who cares.
        contact = pickup.contact_phone or getattr(pickup.requester, 'phone', None)
        if contact:
            notify('pickup.completed',
                   recipients=[('sender', contact)],
                   context={'pickup_no': pickup.pickup_no,
                            'customer_name': pickup.contact_name or 'customer',
                            'collected_count': pickup.collected_count})
        return pickup.to_dict()


@pickup_ns.route('/<int:id>/fail')
class PickupFail(Resource):
    @pickup_ns.doc('fail_pickup')
    @pickup_ns.response(409, 'Pickup already closed')
    @jwt_required()
    def post(self, id):
        """Mark a pickup failed with a reason (assigned rider or admin)."""
        user = _caller()
        pickup = db.session.get(Pickup, id)
        if not pickup:
            pickup_ns.abort(404, 'Pickup not found')
        rider = _rider_profile(user)
        if not user.is_admin and not (rider and pickup.rider_id == rider.id):
            pickup_ns.abort(403, 'Only the assigned rider may fail this pickup')
        if pickup.status in (Pickup.COMPLETED, Pickup.CANCELLED):
            pickup_ns.abort(409, f'A {pickup.status} pickup cannot be failed')
        data = json_object()
        pickup.failure_reason = text(data.get('failure_reason', ''), 'failure_reason', 255, required=True)
        pickup.status = Pickup.FAILED
        pickup.completed_at = datetime.utcnow()
        db.session.commit()
        return pickup.to_dict()
