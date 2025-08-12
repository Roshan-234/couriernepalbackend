from flask_restx import Namespace, Resource, fields
from app.models.tracking_event import TrackingEvent
from app.models.shipment import Shipment
from app.extensions import db

tracking_ns = Namespace('tracking', description='Tracking operations')

# Models
tracking_event_model = tracking_ns.model('TrackingEvent', {
    'id': fields.Integer(readonly=True, description='Tracking event ID'),
    'shipment_id': fields.Integer(required=True, description='Associated shipment ID'),
    'location': fields.String(required=True, description='Event location'),
    'status': fields.String(required=True, description='Event status'),
    'description': fields.String(required=True, description='Event description'),
    'timestamp': fields.DateTime(required=True, description='Event timestamp')
})

shipment_tracking_model = tracking_ns.model('ShipmentTracking', {
    'tracking_number': fields.String(readonly=True, description='Tracking number'),
    'status': fields.String(readonly=True, description='Current status'),
    'estimated_delivery': fields.DateTime(readonly=True, description='Estimated delivery date'),
    'events': fields.List(fields.Nested(tracking_event_model), readonly=True, description='Tracking events')
})

@tracking_ns.route('/<string:tracking_number>')
@tracking_ns.response(404, 'Shipment not found')
@tracking_ns.param('tracking_number', 'The shipment tracking number')
class TrackingInfo(Resource):
    @tracking_ns.doc('get_tracking_info')
    @tracking_ns.marshal_with(shipment_tracking_model)
    def get(self, tracking_number):
        """Get tracking information for a shipment"""
        shipment = Shipment.query.filter_by(tracking_number=tracking_number).first_or_404()
        events = TrackingEvent.query.filter_by(shipment_id=shipment.id).order_by(TrackingEvent.timestamp.desc()).all()
        return {
            'tracking_number': shipment.tracking_number,
            'status': shipment.status,
            'estimated_delivery': shipment.estimated_delivery,
            'events': events
        }
