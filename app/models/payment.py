from datetime import datetime
from app.extensions import db
from app.crypto import EncryptedField, mask


class PaymentGateway(db.Model):
    """Configuration for a payment provider.

    Nepal's wallets and bank rails each want slightly different fields, so
    rather than hard-coding one integration the connection details are data.
    Credentials are encrypted at rest and never serialized in clear.

    Only configuration lives here. No live call is made to a provider yet:
    these are the fields an integration will read when it is switched on.
    """

    __tablename__ = 'payment_gateways'

    #: Providers in common use for Nepali merchants, plus manual rails.
    KNOWN_CODES = (
        'esewa', 'khalti', 'fonepay', 'connectips', 'imepay', 'prabhupay',
        'nepalpay', 'stripe', 'paypal', 'card', 'bank_transfer', 'cash', 'cheque',
    )

    id               = db.Column(db.Integer, primary_key=True)
    code             = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name             = db.Column(db.String(120), nullable=False)
    display_name     = db.Column(db.String(120))
    logo_url         = db.Column(db.String(500))
    instructions     = db.Column(db.Text)

    is_active        = db.Column(db.Boolean, default=False, index=True)
    is_sandbox       = db.Column(db.Boolean, default=True)
    sort_order       = db.Column(db.Integer, default=0)

    # Endpoints
    api_base_url     = db.Column(db.String(500))
    sandbox_base_url = db.Column(db.String(500))
    initiate_path    = db.Column(db.String(300))
    verify_path      = db.Column(db.String(300))
    refund_path      = db.Column(db.String(300))
    status_path      = db.Column(db.String(300))

    # Redirects the provider sends the payer back to
    success_url      = db.Column(db.String(500))
    failure_url      = db.Column(db.String(500))
    callback_url     = db.Column(db.String(500))

    # Identity and credentials (ciphertext columns)
    merchant_code    = db.Column(db.String(120))
    client_id        = db.Column(db.String(255))
    public_key_encrypted    = db.Column(db.Text)
    secret_key_encrypted    = db.Column(db.Text)
    client_secret_encrypted = db.Column(db.Text)
    signature_algo   = db.Column(db.String(30), default='HMAC-SHA256')
    signature_header = db.Column(db.String(80))

    # Commercial rules
    supported_currencies = db.Column(db.JSON, default=lambda: ['NPR'])
    min_amount       = db.Column(db.Float, default=10.0)
    max_amount       = db.Column(db.Float)
    service_charge_percent = db.Column(db.Float, default=0.0)
    service_charge_flat    = db.Column(db.Float, default=0.0)

    # Reliability
    timeout_seconds  = db.Column(db.Integer, default=30)
    max_retries      = db.Column(db.Integer, default=2)

    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    public_key    = EncryptedField('public_key_encrypted')
    secret_key    = EncryptedField('secret_key_encrypted')
    client_secret = EncryptedField('client_secret_encrypted')

    payments = db.relationship('Payment', back_populates='gateway')

    @property
    def base_url(self):
        return (self.sandbox_base_url if self.is_sandbox else self.api_base_url) or self.api_base_url

    @property
    def is_configured(self):
        return bool(self.base_url and (self.secret_key_encrypted or self.merchant_code))

    def to_dict(self, include_secrets=False):
        d = {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "display_name": self.display_name or self.name,
            "logo_url": self.logo_url,
            "instructions": self.instructions,
            "is_active": self.is_active,
            "is_sandbox": self.is_sandbox,
            "sort_order": self.sort_order,
            "supported_currencies": self.supported_currencies or ['NPR'],
            "min_amount": self.min_amount,
            "max_amount": self.max_amount,
            "service_charge_percent": self.service_charge_percent,
            "service_charge_flat": self.service_charge_flat,
            "is_configured": self.is_configured,
        }
        if include_secrets:
            d.update({
                "api_base_url": self.api_base_url,
                "sandbox_base_url": self.sandbox_base_url,
                "initiate_path": self.initiate_path,
                "verify_path": self.verify_path,
                "refund_path": self.refund_path,
                "status_path": self.status_path,
                "success_url": self.success_url,
                "failure_url": self.failure_url,
                "callback_url": self.callback_url,
                "merchant_code": self.merchant_code,
                "client_id": self.client_id,
                "signature_algo": self.signature_algo,
                "signature_header": self.signature_header,
                "timeout_seconds": self.timeout_seconds,
                "max_retries": self.max_retries,
                # Masked, never the raw value.
                "public_key_masked": mask(self.public_key_encrypted),
                "secret_key_masked": mask(self.secret_key_encrypted),
                "client_secret_masked": mask(self.client_secret_encrypted),
            })
        return d


class Payment(db.Model):
    """A single attempt to move money for a shipment or invoice."""

    __tablename__ = 'payments'

    INITIATED = 'initiated'
    PENDING = 'pending'
    SUCCESS = 'success'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    REFUNDED = 'refunded'
    STATUSES = (INITIATED, PENDING, SUCCESS, FAILED, CANCELLED, REFUNDED)

    id              = db.Column(db.Integer, primary_key=True)
    payment_no      = db.Column(db.String(40), unique=True, nullable=False, index=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    shipment_id     = db.Column(db.Integer, db.ForeignKey('shipments.id'), index=True)
    invoice_id      = db.Column(db.Integer, db.ForeignKey('invoices.id'), index=True)
    gateway_id      = db.Column(db.Integer, db.ForeignKey('payment_gateways.id'))

    amount          = db.Column(db.Float, nullable=False, default=0.0)
    currency        = db.Column(db.String(10), default='NPR')
    service_charge  = db.Column(db.Float, default=0.0)
    status          = db.Column(db.String(30), default=INITIATED, index=True)

    # Provider correlation
    transaction_id    = db.Column(db.String(120), index=True)
    gateway_reference = db.Column(db.String(160), index=True)
    gateway_response  = db.Column(db.JSON)
    failure_reason    = db.Column(db.String(255))

    #: Guards against a double-charge when a client retries a request.
    idempotency_key = db.Column(db.String(80), unique=True, index=True)

    paid_at         = db.Column(db.DateTime)
    refunded_at     = db.Column(db.DateTime)
    refund_amount   = db.Column(db.Float, default=0.0)
    refund_reason   = db.Column(db.String(255))

    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user     = db.relationship('User')
    shipment = db.relationship('Shipment', back_populates='payments')
    invoice  = db.relationship('Invoice')
    gateway  = db.relationship('PaymentGateway', back_populates='payments')

    @staticmethod
    def generate_number():
        from secrets import token_hex
        return f"PAY-{datetime.utcnow().strftime('%y%m')}-{token_hex(4).upper()}"

    def to_dict(self):
        return {
            "id": self.id,
            "payment_no": self.payment_no,
            "user_id": self.user_id,
            "shipment_id": self.shipment_id,
            "invoice_id": self.invoice_id,
            "gateway_id": self.gateway_id,
            "gateway_code": self.gateway.code if self.gateway else None,
            "amount": self.amount,
            "currency": self.currency,
            "service_charge": self.service_charge,
            "status": self.status,
            "transaction_id": self.transaction_id,
            "gateway_reference": self.gateway_reference,
            "failure_reason": self.failure_reason,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "refunded_at": self.refunded_at.isoformat() if self.refunded_at else None,
            "refund_amount": self.refund_amount,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
