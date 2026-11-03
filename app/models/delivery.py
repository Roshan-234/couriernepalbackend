from datetime import datetime
from app.extensions import db


class DeliveryAttempt(db.Model):
    """One recorded attempt to hand a parcel over.

    ``delivered`` as a bare status loses the two things disputes turn on: how
    many times someone tried, and why it did not work. Each attempt is kept so
    a return to sender can be justified and rider performance measured.
    """

    __tablename__ = 'delivery_attempts'

    DELIVERED = 'delivered'
    FAILED = 'failed'
    RESCHEDULED = 'rescheduled'
    OUTCOMES = (DELIVERED, FAILED, RESCHEDULED)

    #: Reason codes operations can report on, rather than free text.
    REASONS = (
        'recipient_unavailable',
        'address_incorrect',
        'address_not_found',
        'recipient_refused',
        'payment_not_ready',
        'business_closed',
        'rescheduled_by_recipient',
        'weather_or_road',
        'security_situation',
        'damaged_in_transit',
        'other',
    )

    id            = db.Column(db.Integer, primary_key=True)
    shipment_id   = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False, index=True)
    rider_id      = db.Column(db.Integer, db.ForeignKey('riders.id'), index=True)
    attempt_no    = db.Column(db.Integer, default=1)
    outcome       = db.Column(db.String(30), default=FAILED, index=True)
    reason_code   = db.Column(db.String(50))
    reason_note   = db.Column(db.Text)
    latitude      = db.Column(db.Float)
    longitude     = db.Column(db.Float)
    next_attempt_date = db.Column(db.Date)
    attempted_at  = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    shipment = db.relationship('Shipment', back_populates='attempts')
    rider    = db.relationship('Rider', back_populates='attempts')

    def to_dict(self):
        return {
            "id": self.id,
            "shipment_id": self.shipment_id,
            "rider_id": self.rider_id,
            "attempt_no": self.attempt_no,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "reason_note": self.reason_note,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "next_attempt_date": self.next_attempt_date.isoformat() if self.next_attempt_date else None,
            "attempted_at": self.attempted_at.isoformat() if self.attempted_at else None,
            "rider": self.rider.to_dict(brief=True) if self.rider else None,
        }


class ProofOfDelivery(db.Model):
    """Evidence that a parcel reached the right person.

    The delivery OTP is stored as a hash, never in clear: a database reader
    should not be able to read out the code that closes a delivery.
    """

    __tablename__ = 'proofs_of_delivery'

    id              = db.Column(db.Integer, primary_key=True)
    shipment_id     = db.Column(db.Integer, db.ForeignKey('shipments.id'),
                                unique=True, nullable=False, index=True)
    rider_id        = db.Column(db.Integer, db.ForeignKey('riders.id'))

    recipient_name     = db.Column(db.String(150))
    recipient_relation = db.Column(db.String(60))   # self, family, neighbour, reception
    recipient_id_ref   = db.Column(db.String(60))   # citizenship/licence reference if taken

    signature_path  = db.Column(db.String(500))
    photo_path      = db.Column(db.String(500))

    otp_hash        = db.Column(db.String(255))
    otp_expires_at  = db.Column(db.DateTime)
    otp_verified_at = db.Column(db.DateTime)

    latitude        = db.Column(db.Float)
    longitude       = db.Column(db.Float)
    notes           = db.Column(db.Text)
    delivered_at    = db.Column(db.DateTime, default=datetime.utcnow)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    shipment = db.relationship('Shipment', back_populates='proof')
    rider    = db.relationship('Rider')

    def set_otp(self, code, ttl_minutes=60):
        from datetime import timedelta
        from app.extensions import bcrypt
        self.otp_hash = bcrypt.generate_password_hash(str(code)).decode("utf-8")
        self.otp_expires_at = datetime.utcnow() + timedelta(minutes=ttl_minutes)
        self.otp_verified_at = None

    def check_otp(self, code):
        from app.extensions import bcrypt
        if not self.otp_hash or not code:
            return False
        if self.otp_expires_at and self.otp_expires_at < datetime.utcnow():
            return False
        return bcrypt.check_password_hash(self.otp_hash, str(code))

    @property
    def otp_required(self):
        return bool(self.otp_hash)

    @property
    def otp_verified(self):
        return self.otp_verified_at is not None

    def to_dict(self):
        return {
            "id": self.id,
            "shipment_id": self.shipment_id,
            "rider_id": self.rider_id,
            "recipient_name": self.recipient_name,
            "recipient_relation": self.recipient_relation,
            "recipient_id_ref": self.recipient_id_ref,
            "signature_path": self.signature_path,
            "photo_path": self.photo_path,
            "otp_required": self.otp_required,
            "otp_verified": self.otp_verified,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "notes": self.notes,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
        }
