from datetime import datetime
from app.extensions import db


class Rider(db.Model):
    """A delivery rider, tied to a login and to a home branch.

    ``AgentProfile`` stayed a stub because no route ever assigned work to it.
    A rider carries the operational state last-mile delivery needs: which
    branch they run out of, what they ride, whether they are on duty, and
    where they last reported from.
    """

    __tablename__ = 'riders'

    AVAILABLE = 'available'
    ON_DUTY = 'on_duty'
    OFF_DUTY = 'off_duty'
    ON_LEAVE = 'on_leave'
    SUSPENDED = 'suspended'
    STATUSES = (AVAILABLE, ON_DUTY, OFF_DUTY, ON_LEAVE, SUSPENDED)

    VEHICLE_TYPES = ('bike', 'scooter', 'cycle', 'van', 'truck', 'foot')

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    rider_code      = db.Column(db.String(30), unique=True, nullable=False, index=True)
    branch_id       = db.Column(db.Integer, db.ForeignKey('branches.id'), index=True)

    # Identity and compliance
    phone           = db.Column(db.String(50))
    national_id     = db.Column(db.String(60))
    license_number  = db.Column(db.String(60))
    license_expiry  = db.Column(db.Date)
    emergency_contact_name  = db.Column(db.String(150))
    emergency_contact_phone = db.Column(db.String(50))

    # Vehicle
    vehicle_type    = db.Column(db.String(30), default='bike')
    vehicle_number  = db.Column(db.String(40))

    # Duty state
    status          = db.Column(db.String(30), default=OFF_DUTY, index=True)
    serves_districts = db.Column(db.JSON, default=list)
    max_daily_capacity = db.Column(db.Integer, default=40)
    handles_cod     = db.Column(db.Boolean, default=True)

    # Last reported position, written by the rider app
    latitude        = db.Column(db.Float)
    longitude       = db.Column(db.Float)
    location_updated_at = db.Column(db.DateTime)

    is_active       = db.Column(db.Boolean, default=True, index=True)
    joined_at       = db.Column(db.DateTime, default=datetime.utcnow)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user      = db.relationship('User', foreign_keys=[user_id], backref=db.backref('rider_profile', uselist=False))
    branch    = db.relationship('Branch', back_populates='riders')
    shipments = db.relationship('Shipment', back_populates='rider')
    pickups   = db.relationship('Pickup', back_populates='rider')
    attempts  = db.relationship('DeliveryAttempt', back_populates='rider')

    @property
    def full_name(self):
        if self.user:
            return f"{self.user.first_name or ''} {self.user.last_name or ''}".strip()
        return self.rider_code

    @property
    def can_take_work(self):
        return self.is_active and self.status in (Rider.AVAILABLE, Rider.ON_DUTY)

    def to_dict(self, brief=False):
        if brief:
            return {
                "id": self.id, "rider_code": self.rider_code,
                "name": self.full_name, "phone": self.phone,
                "status": self.status, "vehicle_type": self.vehicle_type,
            }
        return {
            "id": self.id,
            "user_id": self.user_id,
            "rider_code": self.rider_code,
            "name": self.full_name,
            "branch_id": self.branch_id,
            "phone": self.phone,
            "national_id": self.national_id,
            "license_number": self.license_number,
            "license_expiry": self.license_expiry.isoformat() if self.license_expiry else None,
            "emergency_contact_name": self.emergency_contact_name,
            "emergency_contact_phone": self.emergency_contact_phone,
            "vehicle_type": self.vehicle_type,
            "vehicle_number": self.vehicle_number,
            "status": self.status,
            "serves_districts": self.serves_districts or [],
            "max_daily_capacity": self.max_daily_capacity,
            "handles_cod": self.handles_cod,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "location_updated_at": self.location_updated_at.isoformat() if self.location_updated_at else None,
            "is_active": self.is_active,
            "can_take_work": self.can_take_work,
            "joined_at": self.joined_at.isoformat() if self.joined_at else None,
        }
