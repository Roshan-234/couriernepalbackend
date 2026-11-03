from datetime import datetime
from app.extensions import db
from app.crypto import EncryptedField, mask


class NotificationTemplate(db.Model):
    """The wording sent to a customer for a given event and channel.

    Messages were hard-coded in one place (the password reset) and absent
    everywhere else. Keeping them as rows lets operations reword an SMS
    without a deploy, and carries a Nepali variant beside the English one.

    Bodies use ``{placeholder}`` names listed in ``variables`` so the editor
    can show what is available.
    """

    __tablename__ = 'notification_templates'

    EMAIL = 'email'
    SMS = 'sms'
    PUSH = 'push'
    WHATSAPP = 'whatsapp'
    CHANNELS = (EMAIL, SMS, PUSH, WHATSAPP)

    #: Events worth telling someone about.
    EVENTS = (
        'shipment.created',
        'shipment.picked_up',
        'shipment.in_transit',
        'shipment.out_for_delivery',
        'shipment.delivered',
        'shipment.delivery_failed',
        'shipment.returned',
        'pickup.scheduled',
        'pickup.completed',
        'cod.collected',
        'cod.remitted',
        'payment.succeeded',
        'payment.failed',
        'delivery.otp',
        'account.password_reset',
        'account.welcome',
    )

    id          = db.Column(db.Integer, primary_key=True)
    code        = db.Column(db.String(60), unique=True, nullable=False, index=True)
    name        = db.Column(db.String(150), nullable=False)
    event       = db.Column(db.String(60), nullable=False, index=True)
    channel     = db.Column(db.String(20), nullable=False, index=True)

    subject     = db.Column(db.String(255))
    body_en     = db.Column(db.Text)
    body_ne     = db.Column(db.Text)
    variables   = db.Column(db.JSON, default=list)

    is_active   = db.Column(db.Boolean, default=True, index=True)
    send_to_sender   = db.Column(db.Boolean, default=True)
    send_to_receiver = db.Column(db.Boolean, default=False)

    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def render(self, context, language='en'):
        """Fill the template. Unknown placeholders are left as written."""
        body = (self.body_ne if language == 'ne' and self.body_ne else self.body_en) or ''
        subject = self.subject or ''
        for key, value in (context or {}).items():
            token = '{' + str(key) + '}'
            body = body.replace(token, '' if value is None else str(value))
            subject = subject.replace(token, '' if value is None else str(value))
        return subject, body

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "event": self.event,
            "channel": self.channel,
            "subject": self.subject,
            "body_en": self.body_en,
            "body_ne": self.body_ne,
            "variables": self.variables or [],
            "is_active": self.is_active,
            "send_to_sender": self.send_to_sender,
            "send_to_receiver": self.send_to_receiver,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SmsProvider(db.Model):
    """Credentials and wiring for an SMS gateway.

    Nepali operators are reached through local aggregators, each with its own
    parameter names, so the request shape is configuration rather than code.
    """

    __tablename__ = 'sms_providers'

    KNOWN_CODES = ('sparrow', 'aakash', 'nepalsms', 'managepoint', 'twilio', 'custom')

    id            = db.Column(db.Integer, primary_key=True)
    code          = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name          = db.Column(db.String(120), nullable=False)
    is_active     = db.Column(db.Boolean, default=False, index=True)
    is_sandbox    = db.Column(db.Boolean, default=True)
    is_primary    = db.Column(db.Boolean, default=False)

    api_base_url  = db.Column(db.String(500))
    send_endpoint = db.Column(db.String(300))
    balance_endpoint = db.Column(db.String(300))
    http_method   = db.Column(db.String(10), default='POST')
    content_type  = db.Column(db.String(80), default='application/json')

    token_encrypted  = db.Column(db.Text)
    secret_encrypted = db.Column(db.Text)
    sender_id     = db.Column(db.String(40))
    account_id    = db.Column(db.String(120))

    #: Maps our field names onto the provider's parameter names.
    param_map     = db.Column(db.JSON, default=dict)
    extra_params  = db.Column(db.JSON, default=dict)

    unicode_supported = db.Column(db.Boolean, default=True)
    max_length    = db.Column(db.Integer, default=160)
    cost_per_sms  = db.Column(db.Float, default=0.0)
    timeout_seconds = db.Column(db.Integer, default=15)
    rate_limit_per_minute = db.Column(db.Integer, default=100)

    last_used_at  = db.Column(db.DateTime)
    last_error    = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    token  = EncryptedField('token_encrypted')
    secret = EncryptedField('secret_encrypted')

    @property
    def is_configured(self):
        return bool(self.api_base_url and self.token_encrypted)

    def to_dict(self, include_config=False):
        d = {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "is_active": self.is_active,
            "is_primary": self.is_primary,
            "sender_id": self.sender_id,
            "is_configured": self.is_configured,
        }
        if include_config:
            d.update({
                "is_sandbox": self.is_sandbox,
                "api_base_url": self.api_base_url,
                "send_endpoint": self.send_endpoint,
                "balance_endpoint": self.balance_endpoint,
                "http_method": self.http_method,
                "content_type": self.content_type,
                "account_id": self.account_id,
                "param_map": self.param_map or {},
                "extra_params": self.extra_params or {},
                "unicode_supported": self.unicode_supported,
                "max_length": self.max_length,
                "cost_per_sms": self.cost_per_sms,
                "timeout_seconds": self.timeout_seconds,
                "rate_limit_per_minute": self.rate_limit_per_minute,
                "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
                "last_error": self.last_error,
                "token_masked": mask(self.token_encrypted),
                "secret_masked": mask(self.secret_encrypted),
            })
        return d
