from datetime import datetime
from app.extensions import db
from app.shipment_status import group_of, is_terminal, label as status_label


class Shipment(db.Model):
    __tablename__ = 'shipments'
    __table_args__ = (db.Index('ix_shipments_user_created_id', 'user_id', 'created_at', 'id'),)

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

    # Domestic routing. District names come from app/data/nepal_geo.py so the
    # rate engine, the branch network and the district landing pages all agree.
    origin_district       = db.Column(db.String(60), index=True)
    destination_district  = db.Column(db.String(60), index=True)
    origin_branch_id      = db.Column(db.Integer, db.ForeignKey('branches.id'), index=True)
    destination_branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), index=True)
    current_branch_id     = db.Column(db.Integer, db.ForeignKey('branches.id'))

    # Assignment
    rider_id           = db.Column(db.Integer, db.ForeignKey('riders.id'), index=True)
    pickup_id          = db.Column(db.Integer, db.ForeignKey('pickups.id'))
    merchant_id        = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    reference_no       = db.Column(db.String(100), index=True)   # the merchant's own order id

    # Service level and dimensions
    service_level        = db.Column(db.String(30), default='standard')
    length_cm            = db.Column(db.Float)
    width_cm             = db.Column(db.Float)
    height_cm            = db.Column(db.Float)
    volumetric_weight_kg = db.Column(db.Float, default=0.0)
    chargeable_weight_kg = db.Column(db.Float, default=0.0)

    # Contents and protection
    contents_description = db.Column(db.Text)
    is_fragile         = db.Column(db.Boolean, default=False)
    is_insured         = db.Column(db.Boolean, default=False)
    insurance_amount   = db.Column(db.Float, default=0.0)

    # Cash on delivery
    is_cod             = db.Column(db.Boolean, default=False, index=True)
    cod_amount         = db.Column(db.Float, default=0.0)
    cod_currency       = db.Column(db.String(10), default='NPR')
    cod_status         = db.Column(db.String(30), default='not_applicable', index=True)
    cod_collected_at   = db.Column(db.DateTime)

    # Charges. Always priced server-side; never trusted from the client.
    payment_mode       = db.Column(db.String(20), default='prepaid')
    payment_status     = db.Column(db.String(20), default='unpaid', index=True)
    freight_charge     = db.Column(db.Float, default=0.0)
    surcharge_total    = db.Column(db.Float, default=0.0)
    tax_amount         = db.Column(db.Float, default=0.0)
    total_charge       = db.Column(db.Float, default=0.0)

    #: Whether the figures above are a price or a placeholder.
    #:
    #: Booking is deliberately never refused for want of a tariff — a parcel
    #: in front of a counter clerk gets accepted and priced by hand. But the
    #: charge columns default to 0.0, so an unpriced shipment used to be
    #: indistinguishable from a free one, and an invoice could be raised
    #: against it for nothing. With no international lanes loaded, that was
    #: every shipment on the company's main business.
    #:
    #: `unrated` means nobody has priced this yet. It is not zero.
    PRICED = 'priced'
    UNRATED = 'unrated'
    PRICING_FAILED = 'failed'
    PRICING_STATUSES = (PRICED, UNRATED, PRICING_FAILED)

    pricing_status     = db.Column(db.String(20), default=UNRATED, index=True)
    #: Why, in words, for whoever has to quote it by hand.
    pricing_note       = db.Column(db.String(255))

    @property
    def needs_manual_pricing(self):
        return self.pricing_status != self.PRICED

    # External carrier hand-off
    carrier_id          = db.Column(db.Integer, db.ForeignKey('carrier_connectors.id'))
    carrier_tracking_no = db.Column(db.String(100), index=True)

    # Delivery outcome and SLA
    delivery_attempt_count = db.Column(db.Integer, default=0)
    delivered_at       = db.Column(db.DateTime)
    sla_due_at         = db.Column(db.DateTime)

    # Return to origin
    rto_reason         = db.Column(db.String(255))
    parent_shipment_id = db.Column(db.Integer, db.ForeignKey('shipments.id'))

    # Meta
    estimated_delivery = db.Column(db.DateTime)
    notes              = db.Column(db.Text)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at         = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user         = db.relationship('User',         foreign_keys=[user_id], backref='shipments')
    agent        = db.relationship('AgentProfile', back_populates='shipments')
    service      = db.relationship('Service',      back_populates='shipments')
    parcels      = db.relationship('Parcel',       back_populates='shipment', cascade='all, delete-orphan')
    events       = db.relationship('TrackingEvent',back_populates='shipment',
                                   order_by='TrackingEvent.timestamp.desc()',
                                   cascade='all, delete-orphan')
    packing_lists= db.relationship('PackingList',  back_populates='shipment')
    invoices     = db.relationship('Invoice',      back_populates='shipment')

    origin_branch      = db.relationship('Branch', foreign_keys=[origin_branch_id])
    destination_branch = db.relationship('Branch', foreign_keys=[destination_branch_id])
    current_branch     = db.relationship('Branch', foreign_keys=[current_branch_id])
    rider              = db.relationship('Rider',  back_populates='shipments')
    pickup             = db.relationship('Pickup', back_populates='shipments')
    merchant           = db.relationship('User',   foreign_keys=[merchant_id])
    carrier            = db.relationship('CarrierConnector')
    attempts           = db.relationship('DeliveryAttempt', back_populates='shipment',
                                         order_by='DeliveryAttempt.attempted_at.desc()',
                                         cascade='all, delete-orphan')
    proof              = db.relationship('ProofOfDelivery', back_populates='shipment',
                                         uselist=False, cascade='all, delete-orphan')
    cod_collection     = db.relationship('CodCollection', back_populates='shipment',
                                         uselist=False, cascade='all, delete-orphan')
    payments           = db.relationship('Payment', back_populates='shipment',
                                         cascade='all, delete-orphan')
    return_shipment    = db.relationship('Shipment', remote_side=[id], uselist=False)

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
            # Operational fields
            "origin_district":       self.origin_district,
            "destination_district":  self.destination_district,
            "origin_branch_id":      self.origin_branch_id,
            "destination_branch_id": self.destination_branch_id,
            "current_branch_id":     self.current_branch_id,
            "rider_id":              self.rider_id,
            "pickup_id":             self.pickup_id,
            "merchant_id":           self.merchant_id,
            "reference_no":          self.reference_no,
            "service_level":         self.service_level,
            "length_cm":             self.length_cm,
            "width_cm":              self.width_cm,
            "height_cm":             self.height_cm,
            "volumetric_weight_kg":  self.volumetric_weight_kg,
            "chargeable_weight_kg":  self.chargeable_weight_kg,
            "contents_description":  self.contents_description,
            "is_fragile":            self.is_fragile,
            "is_insured":            self.is_insured,
            "insurance_amount":      self.insurance_amount,
            "is_cod":                self.is_cod,
            "cod_amount":            self.cod_amount,
            "cod_currency":          self.cod_currency,
            "cod_status":            self.cod_status,
            "cod_collected_at":      self.cod_collected_at.isoformat() if self.cod_collected_at else None,
            "payment_mode":          self.payment_mode,
            "payment_status":        self.payment_status,
            "freight_charge":        self.freight_charge,
            "surcharge_total":       self.surcharge_total,
            "tax_amount":            self.tax_amount,
            "total_charge":          self.total_charge,
            "pricing_status":        self.pricing_status,
            "pricing_note":          self.pricing_note,
            "needs_manual_pricing":  self.needs_manual_pricing,
            "carrier_id":            self.carrier_id,
            "carrier_tracking_no":   self.carrier_tracking_no,
            "delivery_attempt_count": self.delivery_attempt_count,
            "delivered_at":          self.delivered_at.isoformat() if self.delivered_at else None,
            "sla_due_at":            self.sla_due_at.isoformat() if self.sla_due_at else None,
            "rto_reason":            self.rto_reason,
            "parent_shipment_id":    self.parent_shipment_id,
            "status_label":          status_label(self.status),
            "status_group":          group_of(self.status),
            "is_terminal":           is_terminal(self.status),
        }
        if include_relations:
            d["parcels"] = [p.to_dict() for p in self.parcels]
            d["events"]  = [e.to_dict() for e in self.events]
            d["service"] = {"id": self.service.id, "name": self.service.name} if self.service else None
            d["attempts"] = [a.to_dict() for a in self.attempts]
            d["proof"] = self.proof.to_dict() if self.proof else None
            d["cod_collection"] = self.cod_collection.to_dict() if self.cod_collection else None
            d["payments"] = [p.to_dict() for p in self.payments]
            d["rider"] = self.rider.to_dict(brief=True) if self.rider else None
            d["origin_branch"] = self.origin_branch.to_dict(brief=True) if self.origin_branch else None
            d["destination_branch"] = self.destination_branch.to_dict(brief=True) if self.destination_branch else None
        return d
