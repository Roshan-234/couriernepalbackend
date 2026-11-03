from datetime import datetime
from app.extensions import db


class Claim(db.Model):
    """A customer dispute over a shipment: loss, damage, delay or shortfall.

    Claims were handled over the phone, which left no record of what was
    promised. Keeping them as rows gives operations a queue, a decision
    history and a payout figure that reconciles against the ledger.
    """

    __tablename__ = 'claims'

    # Lifecycle
    OPEN = 'open'
    UNDER_REVIEW = 'under_review'
    AWAITING_CUSTOMER = 'awaiting_customer'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    SETTLED = 'settled'
    WITHDRAWN = 'withdrawn'
    STATUSES = (OPEN, UNDER_REVIEW, AWAITING_CUSTOMER, APPROVED, REJECTED, SETTLED, WITHDRAWN)

    TYPES = ('lost', 'damaged', 'partial_loss', 'delay', 'wrong_delivery',
             'cod_discrepancy', 'overcharge', 'other')

    id            = db.Column(db.Integer, primary_key=True)
    claim_no      = db.Column(db.String(40), unique=True, nullable=False, index=True)
    shipment_id   = db.Column(db.Integer, db.ForeignKey('shipments.id'), index=True)
    raised_by     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    assigned_to   = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)

    claim_type    = db.Column(db.String(40), default='other', index=True)
    status        = db.Column(db.String(30), default=OPEN, index=True)
    priority      = db.Column(db.String(20), default='normal')

    description   = db.Column(db.Text)
    claimed_amount  = db.Column(db.Float, default=0.0)
    approved_amount = db.Column(db.Float, default=0.0)
    currency      = db.Column(db.String(10), default='NPR')
    evidence      = db.Column(db.JSON, default=list)   # uploaded file references

    resolution_note = db.Column(db.Text)
    rejection_reason = db.Column(db.String(255))
    resolved_by   = db.Column(db.Integer, db.ForeignKey('users.id'))
    resolved_at   = db.Column(db.DateTime)
    settled_at    = db.Column(db.DateTime)
    due_at        = db.Column(db.DateTime)

    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shipment  = db.relationship('Shipment')
    raiser    = db.relationship('User', foreign_keys=[raised_by])
    assignee  = db.relationship('User', foreign_keys=[assigned_to])
    resolver  = db.relationship('User', foreign_keys=[resolved_by])

    @staticmethod
    def generate_number():
        from secrets import token_hex
        return f"CLM-{datetime.utcnow().strftime('%y%m')}-{token_hex(3).upper()}"

    @property
    def is_closed(self):
        return self.status in (Claim.SETTLED, Claim.REJECTED, Claim.WITHDRAWN)

    def to_dict(self):
        return {
            "id": self.id,
            "claim_no": self.claim_no,
            "shipment_id": self.shipment_id,
            "raised_by": self.raised_by,
            "assigned_to": self.assigned_to,
            "claim_type": self.claim_type,
            "status": self.status,
            "priority": self.priority,
            "description": self.description,
            "claimed_amount": self.claimed_amount,
            "approved_amount": self.approved_amount,
            "currency": self.currency,
            "evidence": self.evidence or [],
            "resolution_note": self.resolution_note,
            "rejection_reason": self.rejection_reason,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "settled_at": self.settled_at.isoformat() if self.settled_at else None,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "is_closed": self.is_closed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AddressBookEntry(db.Model):
    """A saved sender or recipient, so repeat bookings are not retyped."""

    __tablename__ = 'address_book_entries'

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    label         = db.Column(db.String(100))
    contact_name  = db.Column(db.String(150), nullable=False)
    company       = db.Column(db.String(150))
    phone         = db.Column(db.String(50))
    alternate_phone = db.Column(db.String(50))
    email         = db.Column(db.String(200))

    address       = db.Column(db.Text)
    district      = db.Column(db.String(60), index=True)
    city          = db.Column(db.String(100))
    country       = db.Column(db.String(100), default='Nepal')
    postal_code   = db.Column(db.String(20))
    landmark      = db.Column(db.String(255))
    latitude      = db.Column(db.Float)
    longitude     = db.Column(db.Float)

    is_default_sender   = db.Column(db.Boolean, default=False)
    is_default_receiver = db.Column(db.Boolean, default=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref='address_book')

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "label": self.label,
            "contact_name": self.contact_name,
            "company": self.company,
            "phone": self.phone,
            "alternate_phone": self.alternate_phone,
            "email": self.email,
            "address": self.address,
            "district": self.district,
            "city": self.city,
            "country": self.country,
            "postal_code": self.postal_code,
            "landmark": self.landmark,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "is_default_sender": self.is_default_sender,
            "is_default_receiver": self.is_default_receiver,
        }
