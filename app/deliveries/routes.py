"""Delivery attempts and proof of delivery"""
from datetime import datetime
from secrets import randbelow
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.shipment_status import (DELIVERED, DELIVERY_ATTEMPTED, PICKED_UP,
                                  RTO_INITIATED, can_transition, has_reached,
                                  next_statuses)
from app.auth.utils import require_roles
from app.extensions import db
from app.models.delivery import DeliveryAttempt, ProofOfDelivery
from app.models.rider import Rider
from app.models.shipment import Shipment
from app.models.tracking_event import TrackingEvent
from app.models.user import User
from app.validation import json_object, text

delivery_ns = Namespace('deliveries', description='Delivery attempts and proof of delivery')

attempt_in = delivery_ns.model('DeliveryAttemptIn', {
    'outcome':     fields.String(required=True, description=', '.join(DeliveryAttempt.OUTCOMES)),
    'reason_code': fields.String(description=', '.join(DeliveryAttempt.REASONS)),
    'reason_note': fields.String(),
    'latitude':    fields.Float(),
    'longitude':   fields.Float(),
    'next_attempt_date': fields.String(description='YYYY-MM-DD'),
})

pod_in = delivery_ns.model('ProofOfDeliveryIn', {
    'recipient_name':     fields.String(required=True),
    'recipient_relation': fields.String(description='self, family, neighbour, reception'),
    'recipient_id_ref':   fields.String(),
    'signature_path':     fields.String(),
    'photo_path':         fields.String(),
    'latitude':           fields.Float(),
    'longitude':          fields.Float(),
    'notes':              fields.String(),
    'otp':                fields.String(description='Required when an OTP was issued'),
})

rto_in = delivery_ns.model('RtoIn', {
    'rto_reason': fields.String(required=True),
})

_RELATIONS = ('self', 'family', 'neighbour', 'reception', 'colleague', 'security')


def _caller():
    user = db.session.get(User, int(get_jwt_identity()))
    if not user:
        delivery_ns.abort(404, 'User not found')
    return user


def _shipment(id):
    shipment = db.session.get(Shipment, id)
    if not shipment:
        delivery_ns.abort(404, 'Shipment not found')
    return shipment


def _operator_for(shipment):
    """Admins act on any shipment; a rider only on their own assignments.

    Returns (user, rider_or_None).
    """
    user = _caller()
    rider = Rider.query.filter_by(user_id=user.id).first()
    if user.is_admin:
        return user, rider
    if not rider:
        delivery_ns.abort(403, 'Rider or administrator access required')
    if shipment.rider_id != rider.id:
        delivery_ns.abort(403, 'This shipment is not assigned to you')
    return user, rider


def _coords(data):
    for key, bound in (('latitude', 90), ('longitude', 180)):
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > bound:
            delivery_ns.abort(400, f'{key} must be a number between -{bound} and {bound}')
    return data.get('latitude'), data.get('longitude')


def _require_collected(shipment):
    """A delivery outcome only makes sense once the parcel is in our hands.

    The generic status endpoint lets operations skip a scan, but a rider
    reporting an attempt or proof of delivery on a parcel still awaiting
    pickup is recording something that did not happen.
    """
    if not has_reached(shipment.status, PICKED_UP):
        delivery_ns.abort(
            409,
            f"This shipment has not been collected yet (it is "
            f"'{shipment.status}'), so a delivery outcome cannot be recorded",
        )


def _move(shipment, target, description, location=None):
    """Apply a status change and append the customer-visible timeline row."""
    if not can_transition(shipment.status, target):
        delivery_ns.abort(409, f"Cannot move a shipment from '{shipment.status}' to '{target}'")
    shipment.status = target
    db.session.add(TrackingEvent(
        shipment_id=shipment.id,
        status=target,
        location=location or shipment.destination_district or shipment.destination or '',
        description=description,
    ))


@delivery_ns.route('/shipments/<int:id>/attempt')
@delivery_ns.response(404, 'Shipment not found')
@delivery_ns.response(409, 'Illegal status transition')
class DeliveryAttemptCreate(Resource):
    @delivery_ns.doc('record_delivery_attempt')
    @delivery_ns.expect(attempt_in)
    @jwt_required()
    def post(self, id):
        """Record an attempt to hand the parcel over (assigned rider or admin)."""
        shipment = _shipment(id)
        _user, rider = _operator_for(shipment)
        _require_collected(shipment)
        data = json_object()
        outcome = data.get('outcome')
        if outcome not in DeliveryAttempt.OUTCOMES:
            delivery_ns.abort(400, f"outcome must be one of {', '.join(DeliveryAttempt.OUTCOMES)}")
        reason_code = data.get('reason_code')
        if outcome != DeliveryAttempt.DELIVERED and reason_code not in DeliveryAttempt.REASONS:
            delivery_ns.abort(400, f"reason_code must be one of {', '.join(DeliveryAttempt.REASONS)}")
        if outcome == DeliveryAttempt.DELIVERED:
            reason_code = None
            proof = shipment.proof
            # An issued OTP is the gate on handover; it cannot be skipped here.
            if proof and proof.otp_required and not proof.otp_verified:
                delivery_ns.abort(409, 'Verify the delivery OTP through the proof-of-delivery endpoint')
        latitude, longitude = _coords(data)

        target = DELIVERED if outcome == DeliveryAttempt.DELIVERED else DELIVERY_ATTEMPTED
        _move(shipment, target,
              f"Delivery attempt {(shipment.delivery_attempt_count or 0) + 1}: {outcome}"
              + (f" ({reason_code})" if reason_code else ''))

        shipment.delivery_attempt_count = (shipment.delivery_attempt_count or 0) + 1
        if target == DELIVERED and not shipment.delivered_at:
            shipment.delivered_at = datetime.utcnow()
        attempt = DeliveryAttempt(
            shipment_id=shipment.id,
            rider_id=rider.id if rider else shipment.rider_id,
            attempt_no=shipment.delivery_attempt_count,
            outcome=outcome,
            reason_code=reason_code,
            reason_note=text(data.get('reason_note', ''), 'reason_note', 2000),
            latitude=latitude,
            longitude=longitude,
        )
        if data.get('next_attempt_date'):
            try:
                attempt.next_attempt_date = datetime.strptime(str(data['next_attempt_date']), '%Y-%m-%d').date()
            except ValueError:
                delivery_ns.abort(400, 'next_attempt_date must be an ISO date (YYYY-MM-DD)')
        db.session.add(attempt)
        db.session.commit()
        # A failed attempt leaves the shipment at delivery_attempted, from
        # where operations can retry, hold, or start a return.
        return {"attempt": attempt.to_dict(), "shipment_status": shipment.status,
                "delivery_attempt_count": shipment.delivery_attempt_count,
                "next_statuses": next_statuses(shipment.status)}, 201


@delivery_ns.route('/shipments/<int:id>/attempts')
@delivery_ns.response(404, 'Shipment not found')
class DeliveryAttemptList(Resource):
    @delivery_ns.doc('list_delivery_attempts')
    @jwt_required()
    def get(self, id):
        """List attempts for a shipment (owner, assigned rider or admin)."""
        shipment = _shipment(id)
        user = _caller()
        if not user.is_admin and shipment.user_id != user.id:
            rider = Rider.query.filter_by(user_id=user.id).first()
            if not rider or shipment.rider_id != rider.id:
                delivery_ns.abort(403, 'Access denied')
        attempts = shipment.attempts
        return {"items": [a.to_dict() for a in attempts], "total": len(attempts)}


@delivery_ns.route('/shipments/<int:id>/otp')
@delivery_ns.response(404, 'Shipment not found')
class DeliveryOtp(Resource):
    @delivery_ns.doc('issue_delivery_otp')
    @jwt_required()
    def post(self, id):
        """Issue a delivery OTP (assigned rider or admin).

        Only the hash is stored and the body never carries the code — it
        leaves by SMS to the recipient, not through this API.
        """
        shipment = _shipment(id)
        _operator_for(shipment)
        proof = shipment.proof
        if not proof:
            proof = ProofOfDelivery(shipment_id=shipment.id)
            db.session.add(proof)
        if proof.otp_verified_at:
            delivery_ns.abort(409, 'This delivery has already been verified')
        proof.set_otp(f"{randbelow(1000000):06d}")
        db.session.commit()
        return {"msg": "Delivery OTP issued", "shipment_id": shipment.id,
                "otp_required": True,
                "expires_at": proof.otp_expires_at.isoformat() if proof.otp_expires_at else None}, 201


@delivery_ns.route('/shipments/<int:id>/pod')
@delivery_ns.response(404, 'Shipment not found')
@delivery_ns.response(409, 'Illegal status transition')
class ProofOfDeliveryCreate(Resource):
    @delivery_ns.doc('record_proof_of_delivery')
    @delivery_ns.expect(pod_in)
    @jwt_required()
    def post(self, id):
        """Record proof of delivery and close the shipment (assigned rider or admin)."""
        shipment = _shipment(id)
        _user, rider = _operator_for(shipment)
        _require_collected(shipment)
        data = json_object()
        recipient = text(data.get('recipient_name', ''), 'recipient_name', 150, required=True)
        relation = data.get('recipient_relation') or 'self'
        if relation not in _RELATIONS:
            delivery_ns.abort(400, f"recipient_relation must be one of {', '.join(_RELATIONS)}")
        latitude, longitude = _coords(data)

        proof = shipment.proof
        verify_otp = bool(proof and proof.otp_required and not proof.otp_verified)
        if verify_otp and not proof.check_otp(data.get('otp')):
            delivery_ns.abort(400, 'Invalid or expired delivery OTP')

        _move(shipment, DELIVERED, f'Delivered to {recipient} ({relation})')

        if not proof:
            proof = ProofOfDelivery(shipment_id=shipment.id)
            db.session.add(proof)
        if verify_otp:
            proof.otp_verified_at = datetime.utcnow()
        proof.rider_id = rider.id if rider else shipment.rider_id
        proof.recipient_name = recipient
        proof.recipient_relation = relation
        proof.recipient_id_ref = text(data.get('recipient_id_ref', ''), 'recipient_id_ref', 60)
        proof.signature_path = text(data.get('signature_path', ''), 'signature_path', 500)
        proof.photo_path = text(data.get('photo_path', ''), 'photo_path', 500)
        proof.notes = text(data.get('notes', ''), 'notes', 2000)
        proof.latitude = latitude
        proof.longitude = longitude
        proof.delivered_at = datetime.utcnow()
        shipment.delivered_at = proof.delivered_at
        if not shipment.delivery_attempt_count:
            shipment.delivery_attempt_count = 1
        db.session.commit()
        return {"proof": proof.to_dict(), "shipment_status": shipment.status}, 201


@delivery_ns.route('/shipments/<int:id>/rto')
@delivery_ns.response(404, 'Shipment not found')
@delivery_ns.response(409, 'Illegal status transition')
class ReturnToOrigin(Resource):
    @delivery_ns.doc('start_return_to_origin')
    @delivery_ns.expect(rto_in)
    @require_roles('admin', 'super_admin')
    def post(self, id):
        """Start a return to sender (admin)."""
        shipment = _shipment(id)
        data = json_object()
        reason = text(data.get('rto_reason', ''), 'rto_reason', 255, required=True)
        _move(shipment, RTO_INITIATED, f'Return to sender started: {reason}')
        shipment.rto_reason = reason
        db.session.commit()
        return {"shipment_id": shipment.id, "status": shipment.status, "rto_reason": shipment.rto_reason}
