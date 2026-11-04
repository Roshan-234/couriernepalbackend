from flask import request
from app.validation import tracking_update, json_object
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.tracking_event import TrackingEvent
from app.models.shipment import Shipment
from app.extensions import db
from app.notifications.dispatch import notify_status_change
from app.auth.utils import require_roles

tracking_ns = Namespace('tracking', description='Tracking operations')

tracking_event_model = tracking_ns.model('TrackingEvent', {
    'id':          fields.Integer(readonly=True),
    'status':      fields.String(required=True),
    'location':    fields.String(),
    'description': fields.String(),
    'timestamp':   fields.DateTime(readonly=True),
})

add_event_model = tracking_ns.model('AddTrackingEvent', {
    'status':      fields.String(required=True),
    'location':    fields.String(required=True),
    'description': fields.String(),
})

shipment_tracking_model = tracking_ns.model('ShipmentTracking', {
    'tracking_no':         fields.String(),
    'status':              fields.String(),
    'origin':              fields.String(),
    'destination':         fields.String(),
    'created_at':          fields.DateTime(),
    'estimated_delivery':  fields.DateTime(),
    'events':              fields.List(fields.Nested(tracking_event_model)),
})


@tracking_ns.route('/<string:tracking_no>')
@tracking_ns.response(404, 'Shipment not found')
@tracking_ns.param('tracking_no', 'The shipment tracking number')
class TrackingInfo(Resource):
    @tracking_ns.marshal_with(shipment_tracking_model)
    def get(self, tracking_no):
        """Get tracking information for a shipment"""
        shipment = Shipment.query.filter_by(tracking_no=tracking_no).first_or_404()
        return {
            'tracking_no':        shipment.tracking_no,
            'status':             shipment.status,
            'origin':             shipment.origin,
            'destination':        shipment.destination,
            'created_at':         shipment.created_at,
            'estimated_delivery': shipment.estimated_delivery,
            'events':             shipment.events,
        }


@tracking_ns.route('/<string:tracking_no>/events')
class TrackingEventList(Resource):
    @require_roles('admin', 'super_admin', 'agent')
    @tracking_ns.expect(add_event_model)
    @tracking_ns.marshal_with(tracking_event_model, code=201)
    def post(self, tracking_no):
        """Add a tracking event to a shipment (assigned rider, or admin)"""
        from flask_jwt_extended import get_jwt_identity
        from app.shipment_status import AGENT_STATUSES, can_transition, label
        from app.shipments.routes import _is_admin, _rider_for

        shipment = Shipment.query.filter_by(tracking_no=tracking_no).first_or_404()
        data = tracking_update(json_object())
        target = data['status']

        user_id = int(get_jwt_identity())
        if not _is_admin(user_id):
            # Role alone used to be enough here, so any delivery agent could
            # mark a stranger's parcel delivered or cancelled.
            rider = _rider_for(user_id)
            if not rider or shipment.rider_id != rider.id:
                tracking_ns.abort(403, 'This shipment is not assigned to you')
            if target not in AGENT_STATUSES:
                tracking_ns.abort(403, 'That status may only be set by operations')

        if not can_transition(shipment.status, target):
            tracking_ns.abort(409, f"Cannot move a shipment from "
                                   f"'{label(shipment.status)}' to '{label(target)}'")

        event = TrackingEvent(
            shipment_id=shipment.id,
            status=target,
            location=data.get('location', ''),
            description=data.get('description', ''),
        )
        previous_status = shipment.status
        shipment.status = target
        db.session.add(event)
        db.session.commit()
        notify_status_change(shipment, previous_status)
        return event, 201
