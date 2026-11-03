from datetime import datetime
from app.extensions import db


class Pickup(db.Model):
    """A request to collect parcels from a sender.

    Booking a shipment and collecting it are separate events: a merchant with
    thirty orders wants one van visit, not thirty. A pickup groups the
    shipments collected in a single visit and carries its own assignment and
    outcome.
    """

    __tablename__ = 'pickups'

    REQUESTED = 'requested'
    SCHEDULED = 'scheduled'
    ASSIGNED = 'assigned'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    STATUSES = (REQUESTED, SCHEDULED, ASSIGNED, IN_PROGRESS, COMPLETED, FAILED, CANCELLED)

    SLOTS = ('morning', 'midday', 'afternoon', 'evening')

    id              = db.Column(db.Integer, primary_key=True)
    pickup_no       = db.Column(db.String(40), unique=True, nullable=False, index=True)
    requested_by    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    merchant_id     = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    branch_id       = db.Column(db.Integer, db.ForeignKey('branches.id'), index=True)
    rider_id        = db.Column(db.Integer, db.ForeignKey('riders.id'), index=True)

    # Where to collect from
    contact_name    = db.Column(db.String(150))
    contact_phone   = db.Column(db.String(50))
    address         = db.Column(db.Text)
    district        = db.Column(db.String(60), index=True)
    city            = db.Column(db.String(100))
    landmark        = db.Column(db.String(255))
    latitude        = db.Column(db.Float)
    longitude       = db.Column(db.Float)

    # When
    scheduled_date  = db.Column(db.Date, index=True)
    time_slot       = db.Column(db.String(20), default='midday')
    window_start    = db.Column(db.DateTime)
    window_end      = db.Column(db.DateTime)

    # What to expect
    package_count        = db.Column(db.Integer, default=1)
    estimated_weight_kg  = db.Column(db.Float, default=0.0)
    notes                = db.Column(db.Text)

    # Outcome
    status          = db.Column(db.String(30), default=REQUESTED, index=True)
    assigned_at     = db.Column(db.DateTime)
    started_at      = db.Column(db.DateTime)
    completed_at    = db.Column(db.DateTime)
    collected_count = db.Column(db.Integer, default=0)
    failure_reason  = db.Column(db.String(255))

    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    requester = db.relationship('User', foreign_keys=[requested_by])
    merchant  = db.relationship('User', foreign_keys=[merchant_id])
    branch    = db.relationship('Branch')
    rider     = db.relationship('Rider', back_populates='pickups')
    shipments = db.relationship('Shipment', back_populates='pickup')

    @staticmethod
    def generate_number():
        from secrets import token_hex
        return f"PU-{datetime.utcnow().strftime('%y%m')}-{token_hex(3).upper()}"

    def to_dict(self, include_shipments=False):
        d = {
            "id": self.id,
            "pickup_no": self.pickup_no,
            "requested_by": self.requested_by,
            "merchant_id": self.merchant_id,
            "branch_id": self.branch_id,
            "rider_id": self.rider_id,
            "contact_name": self.contact_name,
            "contact_phone": self.contact_phone,
            "address": self.address,
            "district": self.district,
            "city": self.city,
            "landmark": self.landmark,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "scheduled_date": self.scheduled_date.isoformat() if self.scheduled_date else None,
            "time_slot": self.time_slot,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "package_count": self.package_count,
            "estimated_weight_kg": self.estimated_weight_kg,
            "notes": self.notes,
            "status": self.status,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "collected_count": self.collected_count,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "rider": self.rider.to_dict(brief=True) if self.rider else None,
        }
        if include_shipments:
            d["shipments"] = [s.to_dict() for s in self.shipments]
        return d
