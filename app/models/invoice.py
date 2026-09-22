"""
Commercial Invoice model.

Each item dict shape:
  {
    "sl": 1,
    "description": "Cotton T-Shirts",
    "quantity": 50,
    "unit": "pcs",
    "unit_price": 5.00,
    "total_price": 250.00,
    "currency": "USD",
    "hs_code": "6109.10",
    "country_of_origin": "Nepal"
  }
"""
import uuid
from datetime import datetime, timedelta
from app.extensions import db


class Invoice(db.Model):
    __tablename__ = 'invoices'

    id               = db.Column(db.Integer, primary_key=True)
    invoice_number   = db.Column(db.String(50), unique=True, nullable=False, index=True)

    # Links
    shipment_id      = db.Column(db.Integer, db.ForeignKey('shipments.id'),    nullable=True, index=True)
    packing_list_id  = db.Column(db.Integer, db.ForeignKey('packing_lists.id'),nullable=True)
    created_by       = db.Column(db.Integer, db.ForeignKey('users.id'),        nullable=False)

    # Seller (exporter)
    seller_name      = db.Column(db.String(200), nullable=False)
    seller_address   = db.Column(db.Text)
    seller_phone     = db.Column(db.String(50))
    seller_country   = db.Column(db.String(100), default='Nepal')
    seller_tin       = db.Column(db.String(100))   # Tax / VAT number

    # Buyer (importer)
    buyer_name       = db.Column(db.String(200), nullable=False)
    buyer_address    = db.Column(db.Text)
    buyer_phone      = db.Column(db.String(50))
    buyer_country    = db.Column(db.String(100))
    buyer_tin        = db.Column(db.String(100))

    # Items (JSON)
    items            = db.Column(db.JSON, nullable=False, default=list)

    # Financials
    currency         = db.Column(db.String(10), default='USD')
    subtotal         = db.Column(db.Float, default=0.0)
    discount         = db.Column(db.Float, default=0.0)
    tax_percentage   = db.Column(db.Float, default=0.0)
    tax_amount       = db.Column(db.Float, default=0.0)
    shipping_charge  = db.Column(db.Float, default=0.0)
    total_amount     = db.Column(db.Float, default=0.0)

    # Terms
    payment_terms    = db.Column(db.String(100), default='Prepaid')
    incoterms        = db.Column(db.String(20))   # FOB, CIF, EXW, etc.
    port_of_loading  = db.Column(db.String(100))
    port_of_discharge= db.Column(db.String(100))

    # Generated file
    file_path        = db.Column(db.String(500))
    file_name        = db.Column(db.String(255))

    status           = db.Column(db.String(30), default='draft', index=True)  # draft | issued | paid | cancelled
    due_date         = db.Column(db.DateTime)
    notes            = db.Column(db.Text)

    created_at       = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    shipment         = db.relationship('Shipment',    back_populates='invoices')
    packing_list     = db.relationship('PackingList', back_populates='invoices')
    creator          = db.relationship('User',        foreign_keys=[created_by])

    @staticmethod
    def generate_number():
        """INV-YYYYMM-XXXXXX"""
        prefix = f"INV-{datetime.utcnow().strftime('%Y%m')}-"
        suffix = uuid.uuid4().hex[:6].upper()
        return prefix + suffix

    def to_dict(self):
        return {
            "id":               self.id,
            "invoice_number":   self.invoice_number,
            "shipment_id":      self.shipment_id,
            "packing_list_id":  self.packing_list_id,
            "status":           self.status,
            "seller_name":      self.seller_name,
            "seller_address":   self.seller_address,
            "seller_phone":     self.seller_phone,
            "seller_country":   self.seller_country,
            "seller_tin":       self.seller_tin,
            "buyer_name":       self.buyer_name,
            "buyer_address":    self.buyer_address,
            "buyer_phone":      self.buyer_phone,
            "buyer_country":    self.buyer_country,
            "buyer_tin":        self.buyer_tin,
            "items":            self.items or [],
            "currency":         self.currency,
            "subtotal":         self.subtotal,
            "discount":         self.discount,
            "tax_percentage":   self.tax_percentage,
            "tax_amount":       self.tax_amount,
            "shipping_charge":  self.shipping_charge,
            "total_amount":     self.total_amount,
            "payment_terms":    self.payment_terms,
            "incoterms":        self.incoterms,
            "port_of_loading":  self.port_of_loading,
            "port_of_discharge":self.port_of_discharge,
            "file_name":        self.file_name,
            "due_date":         self.due_date.isoformat() if self.due_date else None,
            "notes":            self.notes,
            "created_at":       self.created_at.isoformat(),
            "updated_at":       self.updated_at.isoformat() if self.updated_at else None,
            "created_by":       self.created_by,
        }
