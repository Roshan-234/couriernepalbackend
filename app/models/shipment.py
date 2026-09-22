from datetime import datetime
from app.extensions import db


class Shipment(db.Model):
    __tablename__ = 'shipments'

    id                 = db.Column(db.Integer, primary_key=True)
    tracking_no        = db.Column(db.String(50), unique=True, nullable=False, index=True)
    user_id            = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    agent_id           = db.Column(db.Integer, db.ForeignKey('agent_profiles.id'))
    service_id         = db.Column(db.Integer, db.ForeignKey('services.id'))
    status             = db.Column(db.String(50), default='pending', index=True)

    # Route
    origin             = db.Column(db.String(255))
    destination        = db.Column(db.String(255))
    origin_country     = db.Column(db.String(100), default='Nepal')
    destination_country= db.Column(db.String(100))

    # Sender (shipper)
    sender_name        = db.Column(db.String(200))
    sender_address     = db.Column(db.Text)
    sender_phone       = db.Column(db.String(50))
    sender_email       = db.Column(db.String(200))

    # Receiver (consignee)
    receiver_name      = db.Column(db.String(200))
    receiver_address   = db.Column(db.Text)
    receiver_phone     = db.Column(db.String(50))
    receiver_email     = db.Column(db.String(200))

    # Cargo
    declared_value     = db.Column(db.Float, default=0.0)
    currency           = db.Column(db.String(10), default='USD')
    incoterms          = db.Column(db.String(20))          # FOB, CIF, etc.
    total_weight_kg    = db.Column(db.Float, default=0.0)
    total_packages     = db.Column(db.Integer, default=1)

    # Meta
    estimated_delivery = db.Column(db.DateTime)
    notes              = db.Column(db.Text)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at         = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user         = db.relationship('User',         backref='shipments')
    agent        = db.relationship('AgentProfile', back_populates='shipments')
    service      = db.relationship('Service',      back_populates='shipments')
    parcels      = db.relationship('Parcel',       back_populates='shipment', cascade='all, delete-orphan')
    events       = db.relationship('TrackingEvent',back_populates='shipment',
                                   order_by='TrackingEvent.timestamp.desc()',
                                   cascade='all, delete-orphan')
    packing_lists= db.relationship('PackingList',  back_populates='shipment')
    invoices     = db.relationship('Invoice',      back_populates='shipment')

    def to_dict(self, include_relations=False):
        d = {
            "id":                  self.id,
            "tracking_no":         self.tracking_no,
            "status":              self.status,
            "origin":              self.origin,
            "destination":         self.destination,
            "origin_country":      self.origin_country,
            "destination_country": self.destination_country,
            "sender_name":         self.sender_name,
            "sender_address":      self.sender_address,
            "sender_phone":        self.sender_phone,
            "sender_email":        self.sender_email,
            "receiver_name":       self.receiver_name,
            "receiver_address":    self.receiver_address,
            "receiver_phone":      self.receiver_phone,
            "receiver_email":      self.receiver_email,
            "declared_value":      self.declared_value,
            "currency":            self.currency,
            "incoterms":           self.incoterms,
            "total_weight_kg":     self.total_weight_kg,
            "total_packages":      self.total_packages,
            "estimated_delivery":  self.estimated_delivery.isoformat() if self.estimated_delivery else None,
            "notes":               self.notes,
            "created_at":          self.created_at.isoformat(),
            "updated_at":          self.updated_at.isoformat() if self.updated_at else None,
            "user_id":             self.user_id,
            "service_id":          self.service_id,
        }
        if include_relations:
            d["parcels"] = [p.to_dict() for p in self.parcels]
            d["events"]  = [e.to_dict() for e in self.events]
            d["service"] = {"id": self.service.id, "name": self.service.name} if self.service else None
        return d
