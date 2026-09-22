from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.tracking_event import TrackingEvent
from app.models.shipment import Shipment
from app.extensions import db
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
    @require_roles('super_admin', 'agent')
    @tracking_ns.expect(add_event_model)
    @tracking_ns.marshal_with(tracking_event_model, code=201)
    def post(self, tracking_no):
        """Add a tracking event to a shipment (agent/admin only)"""
        shipment = Shipment.query.filter_by(tracking_no=tracking_no).first_or_404()
        data = request.get_json()
        event = TrackingEvent(
            shipment_id=shipment.id,
            status=data['status'],
            location=data.get('location', ''),
            description=data.get('description', ''),
        )
        shipment.status = data['status']
        db.session.add(event)
        db.session.commit()
        return event, 201
