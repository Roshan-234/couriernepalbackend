"""
PackingList — one packing list per shipment (or standalone).
Items are stored as JSON so the schema stays flexible.

Each item dict shape:
  {
    "sl": 1,
    "description": "Cotton T-Shirts",
    "quantity": 50,
    "unit": "pcs",
    "unit_weight_kg": 0.25,
    "total_weight_kg": 12.5,
    "country_of_origin": "Nepal",
    "hs_code": "6109.10"
  }
"""
import uuid
from datetime import datetime
from app.extensions import db


class PackingList(db.Model):
    __tablename__ = 'packing_lists'

    id              = db.Column(db.Integer, primary_key=True)
    pl_number       = db.Column(db.String(50), unique=True, nullable=False, index=True)

    # Links
    shipment_id     = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=True, index=True)
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Sender (shipper / exporter)
    sender_name     = db.Column(db.String(200), nullable=False)
    sender_address  = db.Column(db.Text)
    sender_phone    = db.Column(db.String(50))
    sender_country  = db.Column(db.String(100), default='Nepal')

    # Receiver (consignee / importer)
    receiver_name   = db.Column(db.String(200), nullable=False)
    receiver_address= db.Column(db.Text)
    receiver_phone  = db.Column(db.String(50))
    receiver_country= db.Column(db.String(100))

    # Cargo items (JSON array)
    items           = db.Column(db.JSON, nullable=False, default=list)

    # Totals (denormalised for fast display)
    total_packages  = db.Column(db.Integer, default=0)
    total_weight_kg = db.Column(db.Float, default=0.0)
    total_items     = db.Column(db.Integer, default=0)

    # Generated file
    file_path       = db.Column(db.String(500))
    file_name       = db.Column(db.String(255))

    notes           = db.Column(db.Text)
    status          = db.Column(db.String(30), default='draft', index=True)   # draft | finalized

    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    shipment        = db.relationship('Shipment',  back_populates='packing_lists')
    creator         = db.relationship('User',      foreign_keys=[created_by])
    invoices        = db.relationship('Invoice',   back_populates='packing_list')

    @staticmethod
    def generate_number():
        """PL-YYYYMM-XXXXXX"""
        prefix = f"PL-{datetime.utcnow().strftime('%Y%m')}-"
        suffix = uuid.uuid4().hex[:6].upper()
        return prefix + suffix

    def to_dict(self):
        return {
            "id":               self.id,
            "pl_number":        self.pl_number,
            "shipment_id":      self.shipment_id,
            "status":           self.status,
            "sender_name":      self.sender_name,
            "sender_address":   self.sender_address,
            "sender_phone":     self.sender_phone,
            "sender_country":   self.sender_country,
            "receiver_name":    self.receiver_name,
            "receiver_address": self.receiver_address,
            "receiver_phone":   self.receiver_phone,
            "receiver_country": self.receiver_country,
            "items":            self.items or [],
            "total_packages":   self.total_packages,
            "total_weight_kg":  self.total_weight_kg,
            "total_items":      self.total_items,
            "file_name":        self.file_name,
            "notes":            self.notes,
            "created_at":       self.created_at.isoformat(),
            "updated_at":       self.updated_at.isoformat() if self.updated_at else None,
            "created_by":       self.created_by,
        }
