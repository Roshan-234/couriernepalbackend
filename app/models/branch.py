from datetime import datetime
from app.extensions import db


class Branch(db.Model):
    """A physical node in the delivery network.

    Warehouses stayed a disconnected address book because nothing routed
    through them. A branch is the thing shipments actually move between: a
    parcel is booked at one, railed through hubs, and delivered from another.
    """

    __tablename__ = 'branches'

    HUB = 'hub'
    BRANCH = 'branch'
    PICKUP_POINT = 'pickup_point'
    FRANCHISE = 'franchise'
    TYPES = (HUB, BRANCH, PICKUP_POINT, FRANCHISE)

    id              = db.Column(db.Integer, primary_key=True)
    code            = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name            = db.Column(db.String(150), nullable=False)
    type            = db.Column(db.String(30), default=BRANCH, index=True)

    # Location. district matches app/data/nepal_geo.py so rate zones resolve.
    district        = db.Column(db.String(60), index=True)
    province        = db.Column(db.String(60))
    city            = db.Column(db.String(100))
    address         = db.Column(db.Text)
    landmark        = db.Column(db.String(255))
    postal_code     = db.Column(db.String(20))
    latitude        = db.Column(db.Float)
    longitude       = db.Column(db.Float)

    # Contact
    phone           = db.Column(db.String(50))
    alternate_phone = db.Column(db.String(50))
    email           = db.Column(db.String(200))
    manager_name    = db.Column(db.String(150))
    manager_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Operations
    opening_hours   = db.Column(db.String(255), default='Sun-Fri 09:00-18:00')
    serves_districts = db.Column(db.JSON, default=list)   # districts this branch delivers to
    parent_branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'))
    accepts_walk_in = db.Column(db.Boolean, default=True)
    handles_cod     = db.Column(db.Boolean, default=True)
    handles_international = db.Column(db.Boolean, default=False)

    is_active       = db.Column(db.Boolean, default=True, index=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    manager       = db.relationship('User', foreign_keys=[manager_user_id])
    parent_branch = db.relationship('Branch', remote_side=[id], backref='child_branches')
    riders        = db.relationship('Rider', back_populates='branch')

    def serves(self, district_name):
        """True when this branch delivers to the given district."""
        if self.district and district_name == self.district:
            return True
        return district_name in (self.serves_districts or [])

    def to_dict(self, brief=False):
        if brief:
            return {
                "id": self.id, "code": self.code, "name": self.name,
                "type": self.type, "district": self.district, "city": self.city,
            }
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "type": self.type,
            "district": self.district,
            "province": self.province,
            "city": self.city,
            "address": self.address,
            "landmark": self.landmark,
            "postal_code": self.postal_code,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "phone": self.phone,
            "alternate_phone": self.alternate_phone,
            "email": self.email,
            "manager_name": self.manager_name,
            "manager_user_id": self.manager_user_id,
            "opening_hours": self.opening_hours,
            "serves_districts": self.serves_districts or [],
            "parent_branch_id": self.parent_branch_id,
            "accepts_walk_in": self.accepts_walk_in,
            "handles_cod": self.handles_cod,
            "handles_international": self.handles_international,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
