from datetime import datetime
from app.extensions import db
from app.crypto import EncryptedField, mask


class WebhookEndpoint(db.Model):
    """A merchant URL we notify when something happens to their shipments.

    Polling the tracking endpoint is how integrations get rate-limited into
    uselessness. An endpoint subscribes to named events and receives a signed
    POST instead. Repeated failures disable it rather than retrying forever.
    """

    __tablename__ = 'webhook_endpoints'

    #: Events a merchant can subscribe to.
    EVENTS = (
        'shipment.created',
        'shipment.status_changed',
        'shipment.picked_up',
        'shipment.out_for_delivery',
        'shipment.delivered',
        'shipment.delivery_failed',
        'shipment.returned',
        'shipment.cancelled',
        'pickup.scheduled',
        'pickup.completed',
        'cod.collected',
        'cod.remitted',
        'payment.succeeded',
        'payment.failed',
    )

    id            = db.Column(db.Integer, primary_key=True)
    merchant_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    url           = db.Column(db.String(500), nullable=False)
    description   = db.Column(db.String(255))
    events        = db.Column(db.JSON, default=list)

    secret_encrypted = db.Column(db.Text)
    signature_header = db.Column(db.String(80), default='X-Moonlight-Signature')
    signature_algo   = db.Column(db.String(30), default='HMAC-SHA256')

    is_active     = db.Column(db.Boolean, default=True, index=True)
    timeout_seconds = db.Column(db.Integer, default=10)
    max_retries   = db.Column(db.Integer, default=5)

    consecutive_failures = db.Column(db.Integer, default=0)
    disabled_at      = db.Column(db.DateTime)
    last_success_at  = db.Column(db.DateTime)
    last_failure_at  = db.Column(db.DateTime)
    last_error       = db.Column(db.Text)

    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    secret = EncryptedField('secret_encrypted')

    merchant   = db.relationship('User')
    deliveries = db.relationship('WebhookDelivery', back_populates='endpoint',
                                 cascade='all, delete-orphan')

    @staticmethod
    def generate_secret():
        import secrets
        return f"whsec_{secrets.token_urlsafe(32)}"

    def subscribes_to(self, event):
        subscribed = self.events or []
        return '*' in subscribed or event in subscribed

    def sign(self, body, timestamp):
        """Signature for a payload, as the receiver should recompute it."""
        import hashlib
        import hmac
        key = (self.secret or "").encode("utf-8")
        message = f"{timestamp}.{body}".encode("utf-8")
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def to_dict(self, include_secret_mask=False):
        d = {
            "id": self.id,
            "merchant_id": self.merchant_id,
            "url": self.url,
            "description": self.description,
            "events": self.events or [],
            "signature_header": self.signature_header,
            "signature_algo": self.signature_algo,
            "is_active": self.is_active,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "consecutive_failures": self.consecutive_failures,
            "disabled_at": self.disabled_at.isoformat() if self.disabled_at else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_secret_mask:
            d["secret_masked"] = mask(self.secret_encrypted)
        return d


class WebhookDelivery(db.Model):
    """One attempt to deliver one event to one endpoint.

    Kept so a merchant can see what we sent, what came back, and replay it.
    """

    __tablename__ = 'webhook_deliveries'

    PENDING = 'pending'
    DELIVERED = 'delivered'
    FAILED = 'failed'
    EXHAUSTED = 'exhausted'
    STATUSES = (PENDING, DELIVERED, FAILED, EXHAUSTED)

    id            = db.Column(db.Integer, primary_key=True)
    endpoint_id   = db.Column(db.Integer, db.ForeignKey('webhook_endpoints.id'),
                              nullable=False, index=True)
    event_type    = db.Column(db.String(60), nullable=False, index=True)
    event_id      = db.Column(db.String(60), unique=True, index=True)
    payload       = db.Column(db.JSON)

    status        = db.Column(db.String(20), default=PENDING, index=True)
    attempt_count = db.Column(db.Integer, default=0)
    response_status = db.Column(db.Integer)
    response_body = db.Column(db.Text)
    error         = db.Column(db.Text)

    next_retry_at = db.Column(db.DateTime, index=True)
    delivered_at  = db.Column(db.DateTime)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    endpoint = db.relationship('WebhookEndpoint', back_populates='deliveries')

    @staticmethod
    def generate_event_id():
        import secrets
        return f"evt_{secrets.token_hex(12)}"

    def to_dict(self, include_payload=False):
        d = {
            "id": self.id,
            "endpoint_id": self.endpoint_id,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "response_status": self.response_status,
            "error": self.error,
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_payload:
            d["payload"] = self.payload
            d["response_body"] = self.response_body
        return d
