from datetime import datetime
from app.extensions import db
from app.crypto import EncryptedField, mask


class CarrierConnector(db.Model):
    """Everything needed to talk to an external carrier's API.

    ``TrackingProvider`` only held a public tracking URL, so handing a parcel
    to DHL or Aramex meant copying numbers by hand. This model carries the
    full connection: where the API lives, how to authenticate, which endpoint
    does what, how to shape the request, and how to read the reply back into
    our own tracking events.

    Response mapping is expressed as dotted paths rather than code, so adding
    a carrier is configuration instead of a deployment. ``status_map`` turns
    the carrier's vocabulary into the statuses in ``app/shipment_status.py``.

    No outbound call is made yet. These are the fields a dispatcher will read
    once the integration is switched on, and the credentials are encrypted at
    rest so the table is safe to back up.
    """

    __tablename__ = 'carrier_connectors'

    AUTH_NONE = 'none'
    AUTH_API_KEY = 'api_key'
    AUTH_BASIC = 'basic'
    AUTH_BEARER = 'bearer'
    AUTH_OAUTH2 = 'oauth2'
    AUTH_HMAC = 'hmac'
    AUTH_TYPES = (AUTH_NONE, AUTH_API_KEY, AUTH_BASIC, AUTH_BEARER, AUTH_OAUTH2, AUTH_HMAC)

    HEALTH_UNKNOWN = 'unknown'
    HEALTH_OK = 'ok'
    HEALTH_DEGRADED = 'degraded'
    HEALTH_DOWN = 'down'

    id                = db.Column(db.Integer, primary_key=True)
    code              = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name              = db.Column(db.String(120), nullable=False)
    display_name      = db.Column(db.String(120))
    logo_url          = db.Column(db.String(500))
    website_url       = db.Column(db.String(500))
    instructions      = db.Column(db.Text)
    sort_order        = db.Column(db.Integer, default=0)

    # --- Capability flags -------------------------------------------------
    is_active         = db.Column(db.Boolean, default=False, index=True)
    is_sandbox        = db.Column(db.Boolean, default=True)
    supports_tracking = db.Column(db.Boolean, default=True)
    supports_booking  = db.Column(db.Boolean, default=False)
    supports_label    = db.Column(db.Boolean, default=False)
    supports_rates    = db.Column(db.Boolean, default=False)
    supports_pickup   = db.Column(db.Boolean, default=False)
    supports_cancel   = db.Column(db.Boolean, default=False)
    supports_webhook  = db.Column(db.Boolean, default=False)

    # --- Connection -------------------------------------------------------
    api_base_url      = db.Column(db.String(500))
    sandbox_base_url  = db.Column(db.String(500))
    api_version       = db.Column(db.String(30))
    content_type      = db.Column(db.String(80), default='application/json')
    auth_type         = db.Column(db.String(20), default=AUTH_API_KEY)

    # Where the credential goes on the wire
    auth_header_name   = db.Column(db.String(80), default='Authorization')
    auth_header_prefix = db.Column(db.String(40), default='Bearer')
    auth_query_param   = db.Column(db.String(80))
    extra_headers      = db.Column(db.JSON, default=dict)
    extra_query_params = db.Column(db.JSON, default=dict)

    # --- Credentials (ciphertext columns) ---------------------------------
    account_number          = db.Column(db.String(120))
    username                = db.Column(db.String(160))
    client_id               = db.Column(db.String(255))
    api_key_encrypted       = db.Column(db.Text)
    api_secret_encrypted    = db.Column(db.Text)
    password_encrypted      = db.Column(db.Text)
    client_secret_encrypted = db.Column(db.Text)

    # OAuth2
    oauth_token_url         = db.Column(db.String(500))
    oauth_scope             = db.Column(db.String(255))
    oauth_audience          = db.Column(db.String(255))
    access_token_encrypted  = db.Column(db.Text)
    token_expires_at        = db.Column(db.DateTime)

    # --- Endpoints --------------------------------------------------------
    track_endpoint           = db.Column(db.String(300))
    create_shipment_endpoint = db.Column(db.String(300))
    label_endpoint           = db.Column(db.String(300))
    rate_endpoint            = db.Column(db.String(300))
    pickup_endpoint          = db.Column(db.String(300))
    cancel_endpoint          = db.Column(db.String(300))

    track_method             = db.Column(db.String(10), default='GET')
    create_method            = db.Column(db.String(10), default='POST')

    #: Request body shape, with ``{tracking_no}`` style placeholders.
    track_request_template   = db.Column(db.JSON)
    create_request_template  = db.Column(db.JSON)

    # --- Response mapping (dotted paths into the carrier's JSON) ----------
    tracking_number_path     = db.Column(db.String(200))
    status_path              = db.Column(db.String(200))
    events_path              = db.Column(db.String(200))
    event_status_path        = db.Column(db.String(200))
    event_time_path          = db.Column(db.String(200))
    event_location_path      = db.Column(db.String(200))
    event_description_path   = db.Column(db.String(200))
    event_time_format        = db.Column(db.String(60))
    error_message_path       = db.Column(db.String(200))

    #: Carrier status string -> our status, e.g. {"OK": "delivered"}.
    status_map               = db.Column(db.JSON, default=dict)

    #: Public URL a customer can open, with ``{tracking_no}``.
    tracking_url_template    = db.Column(db.String(500))

    # --- Inbound webhooks -------------------------------------------------
    webhook_url              = db.Column(db.String(500))
    webhook_secret_encrypted = db.Column(db.Text)
    webhook_signature_header = db.Column(db.String(80))
    webhook_signature_algo   = db.Column(db.String(30), default='HMAC-SHA256')

    # --- Reliability and health ------------------------------------------
    timeout_seconds       = db.Column(db.Integer, default=20)
    max_retries           = db.Column(db.Integer, default=3)
    retry_backoff_seconds = db.Column(db.Integer, default=5)
    rate_limit_per_minute = db.Column(db.Integer, default=60)
    poll_interval_minutes = db.Column(db.Integer, default=60)

    health_status        = db.Column(db.String(20), default=HEALTH_UNKNOWN)
    consecutive_failures = db.Column(db.Integer, default=0)
    last_sync_at         = db.Column(db.DateTime)
    last_success_at      = db.Column(db.DateTime)
    last_error           = db.Column(db.Text)

    # --- Coverage ---------------------------------------------------------
    supported_countries = db.Column(db.JSON, default=list)   # empty means all
    supported_services  = db.Column(db.JSON, default=list)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    api_key        = EncryptedField('api_key_encrypted')
    api_secret     = EncryptedField('api_secret_encrypted')
    password       = EncryptedField('password_encrypted')
    client_secret  = EncryptedField('client_secret_encrypted')
    access_token   = EncryptedField('access_token_encrypted')
    webhook_secret = EncryptedField('webhook_secret_encrypted')

    @property
    def base_url(self):
        if self.is_sandbox and self.sandbox_base_url:
            return self.sandbox_base_url
        return self.api_base_url

    @property
    def is_configured(self):
        """True when there is enough here to attempt a call."""
        if not self.base_url:
            return False
        if self.auth_type == self.AUTH_NONE:
            return True
        if self.auth_type in (self.AUTH_API_KEY, self.AUTH_BEARER):
            return bool(self.api_key_encrypted)
        if self.auth_type == self.AUTH_BASIC:
            return bool(self.username and self.password_encrypted)
        if self.auth_type == self.AUTH_OAUTH2:
            return bool(self.client_id and self.client_secret_encrypted and self.oauth_token_url)
        if self.auth_type == self.AUTH_HMAC:
            return bool(self.api_key_encrypted and self.api_secret_encrypted)
        return False

    def public_tracking_url(self, tracking_no):
        if not self.tracking_url_template or not tracking_no:
            return None
        return self.tracking_url_template.replace('{tracking_no}', str(tracking_no))

    def map_status(self, carrier_status, default=None):
        """Translate a carrier's status string into one of ours."""
        if not carrier_status:
            return default
        table = self.status_map or {}
        return table.get(str(carrier_status)) or table.get(str(carrier_status).upper()) \
            or table.get(str(carrier_status).lower()) or default

    def to_dict(self, include_config=False, include_secrets=False):
        """Public shape by default.

        ``include_config`` adds the connection fields for the admin console.
        ``include_secrets`` only ever adds masked values; the plaintext
        credential is never serialized.
        """
        d = {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "display_name": self.display_name or self.name,
            "logo_url": self.logo_url,
            "website_url": self.website_url,
            "instructions": self.instructions,
            "is_active": self.is_active,
            "sort_order": self.sort_order,
            "supported_countries": self.supported_countries or [],
            "supported_services": self.supported_services or [],
            "tracking_url_template": self.tracking_url_template,
            "capabilities": {
                "tracking": self.supports_tracking,
                "booking": self.supports_booking,
                "label": self.supports_label,
                "rates": self.supports_rates,
                "pickup": self.supports_pickup,
                "cancel": self.supports_cancel,
                "webhook": self.supports_webhook,
            },
        }
        if include_config:
            d.update({
                "is_sandbox": self.is_sandbox,
                "api_base_url": self.api_base_url,
                "sandbox_base_url": self.sandbox_base_url,
                "api_version": self.api_version,
                "content_type": self.content_type,
                "auth_type": self.auth_type,
                "auth_header_name": self.auth_header_name,
                "auth_header_prefix": self.auth_header_prefix,
                "auth_query_param": self.auth_query_param,
                "extra_headers": self.extra_headers or {},
                "extra_query_params": self.extra_query_params or {},
                "account_number": self.account_number,
                "username": self.username,
                "client_id": self.client_id,
                "oauth_token_url": self.oauth_token_url,
                "oauth_scope": self.oauth_scope,
                "oauth_audience": self.oauth_audience,
                "token_expires_at": self.token_expires_at.isoformat() if self.token_expires_at else None,
                "track_endpoint": self.track_endpoint,
                "create_shipment_endpoint": self.create_shipment_endpoint,
                "label_endpoint": self.label_endpoint,
                "rate_endpoint": self.rate_endpoint,
                "pickup_endpoint": self.pickup_endpoint,
                "cancel_endpoint": self.cancel_endpoint,
                "track_method": self.track_method,
                "create_method": self.create_method,
                "track_request_template": self.track_request_template,
                "create_request_template": self.create_request_template,
                "tracking_number_path": self.tracking_number_path,
                "status_path": self.status_path,
                "events_path": self.events_path,
                "event_status_path": self.event_status_path,
                "event_time_path": self.event_time_path,
                "event_location_path": self.event_location_path,
                "event_description_path": self.event_description_path,
                "event_time_format": self.event_time_format,
                "error_message_path": self.error_message_path,
                "status_map": self.status_map or {},
                "webhook_url": self.webhook_url,
                "webhook_signature_header": self.webhook_signature_header,
                "webhook_signature_algo": self.webhook_signature_algo,
                "timeout_seconds": self.timeout_seconds,
                "max_retries": self.max_retries,
                "retry_backoff_seconds": self.retry_backoff_seconds,
                "rate_limit_per_minute": self.rate_limit_per_minute,
                "poll_interval_minutes": self.poll_interval_minutes,
                "health_status": self.health_status,
                "consecutive_failures": self.consecutive_failures,
                "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
                "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
                "last_error": self.last_error,
                "is_configured": self.is_configured,
            })
        if include_secrets:
            d.update({
                "api_key_masked": mask(self.api_key_encrypted),
                "api_secret_masked": mask(self.api_secret_encrypted),
                "password_masked": mask(self.password_encrypted),
                "client_secret_masked": mask(self.client_secret_encrypted),
                "webhook_secret_masked": mask(self.webhook_secret_encrypted),
            })
        return d
