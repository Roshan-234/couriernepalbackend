from datetime import datetime
from app.extensions import db


class CodCollection(db.Model):
    """Money a rider collects at the door, and where it goes next.

    Cash on delivery is the default in Nepal, and it makes the courier a
    custodian of someone else's money. Each collection is tracked from the
    doorstep, through the branch deposit, to the merchant remittance, so the
    cash in hand can always be reconciled against the ledger.
    """

    __tablename__ = 'cod_collections'

    PENDING = 'pending'
    COLLECTED = 'collected'
    DEPOSITED = 'deposited'
    REMITTED = 'remitted'
    CANCELLED = 'cancelled'
    STATUSES = (PENDING, COLLECTED, DEPOSITED, REMITTED, CANCELLED)

    MODES = ('cash', 'esewa', 'khalti', 'fonepay', 'connectips', 'imepay', 'card', 'bank_transfer')

    id              = db.Column(db.Integer, primary_key=True)
    shipment_id     = db.Column(db.Integer, db.ForeignKey('shipments.id'),
                                unique=True, nullable=False, index=True)
    merchant_id     = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    branch_id       = db.Column(db.Integer, db.ForeignKey('branches.id'), index=True)
    rider_id        = db.Column(db.Integer, db.ForeignKey('riders.id'), index=True)
    remittance_id   = db.Column(db.Integer, db.ForeignKey('cod_remittances.id'), index=True)

    amount          = db.Column(db.Float, nullable=False, default=0.0)
    currency        = db.Column(db.String(10), default='NPR')
    collection_mode = db.Column(db.String(30), default='cash')
    status          = db.Column(db.String(30), default=PENDING, index=True)

    reference_no    = db.Column(db.String(100))   # wallet/bank transaction reference
    collected_at    = db.Column(db.DateTime, index=True)
    deposited_at    = db.Column(db.DateTime)
    deposit_reference = db.Column(db.String(100))
    verified_by     = db.Column(db.Integer, db.ForeignKey('users.id'))
    verified_at     = db.Column(db.DateTime)
    notes           = db.Column(db.Text)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shipment   = db.relationship('Shipment', back_populates='cod_collection')
    merchant   = db.relationship('User', foreign_keys=[merchant_id])
    verifier   = db.relationship('User', foreign_keys=[verified_by])
    branch     = db.relationship('Branch')
    rider      = db.relationship('Rider')
    remittance = db.relationship('CodRemittance', back_populates='collections')

    def to_dict(self):
        return {
            "id": self.id,
            "shipment_id": self.shipment_id,
            "merchant_id": self.merchant_id,
            "branch_id": self.branch_id,
            "rider_id": self.rider_id,
            "remittance_id": self.remittance_id,
            "amount": self.amount,
            "currency": self.currency,
            "collection_mode": self.collection_mode,
            "status": self.status,
            "reference_no": self.reference_no,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
            "deposited_at": self.deposited_at.isoformat() if self.deposited_at else None,
            "deposit_reference": self.deposit_reference,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CodRemittance(db.Model):
    """A settlement paying a merchant the COD collected on their behalf.

    Totals are derived from the attached collections rather than typed in, so
    the payout and the ledger cannot drift apart.
    """

    __tablename__ = 'cod_remittances'

    DRAFT = 'draft'
    APPROVED = 'approved'
    PAID = 'paid'
    CANCELLED = 'cancelled'
    STATUSES = (DRAFT, APPROVED, PAID, CANCELLED)

    id              = db.Column(db.Integer, primary_key=True)
    remittance_no   = db.Column(db.String(40), unique=True, nullable=False, index=True)
    merchant_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    branch_id       = db.Column(db.Integer, db.ForeignKey('branches.id'))

    period_start    = db.Column(db.Date)
    period_end      = db.Column(db.Date)

    total_collected = db.Column(db.Float, default=0.0)
    service_fee     = db.Column(db.Float, default=0.0)
    tax_amount      = db.Column(db.Float, default=0.0)
    adjustment      = db.Column(db.Float, default=0.0)
    net_payable     = db.Column(db.Float, default=0.0)
    currency        = db.Column(db.String(10), default='NPR')

    status          = db.Column(db.String(30), default=DRAFT, index=True)
    payment_method  = db.Column(db.String(30))      # bank_transfer, esewa, cheque, cash
    payment_reference = db.Column(db.String(100))
    bank_account    = db.Column(db.String(120))

    approved_by     = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_at     = db.Column(db.DateTime)
    paid_at         = db.Column(db.DateTime)
    notes           = db.Column(db.Text)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    merchant    = db.relationship('User', foreign_keys=[merchant_id])
    approver    = db.relationship('User', foreign_keys=[approved_by])
    branch      = db.relationship('Branch')
    collections = db.relationship('CodCollection', back_populates='remittance')

    @staticmethod
    def generate_number():
        from secrets import token_hex
        return f"RM-{datetime.utcnow().strftime('%y%m')}-{token_hex(3).upper()}"

    def recalculate(self):
        """Derive the payout from the attached collections."""
        self.total_collected = round(sum(c.amount or 0.0 for c in self.collections), 2)
        self.net_payable = round(
            self.total_collected
            - (self.service_fee or 0.0)
            - (self.tax_amount or 0.0)
            + (self.adjustment or 0.0),
            2,
        )
        return self.net_payable

    def to_dict(self, include_collections=False):
        d = {
            "id": self.id,
            "remittance_no": self.remittance_no,
            "merchant_id": self.merchant_id,
            "branch_id": self.branch_id,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "total_collected": self.total_collected,
            "service_fee": self.service_fee,
            "tax_amount": self.tax_amount,
            "adjustment": self.adjustment,
            "net_payable": self.net_payable,
            "currency": self.currency,
            "status": self.status,
            "payment_method": self.payment_method,
            "payment_reference": self.payment_reference,
            "bank_account": self.bank_account,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "notes": self.notes,
            "collection_count": len(self.collections),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_collections:
            d["collections"] = [c.to_dict() for c in self.collections]
        return d
