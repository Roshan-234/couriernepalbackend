"""External carrier API connectors.

Supersedes ``app/tracking_providers``: that model held a public tracking URL
and nothing else, so handing a parcel to DHL meant copying numbers by hand.
:class:`~app.models.carrier.CarrierConnector` carries the whole connection,
and these routes are the admin console over it.

No outbound HTTP call happens here. ``call_carrier`` below is the single
seam where one will go; until it is switched on it reports that live calls
are disabled rather than pretending a request succeeded. ``POST
/<code>/test`` therefore validates the *configuration* -- it never touches
the network, so it is safe to run against a half-filled connector.
"""

import ipaddress
from datetime import datetime
from urllib.parse import urlparse

from flask_restx import Namespace, Resource, fields

from app.auth.utils import require_roles
from app.extensions import db
from app.models.carrier import CarrierConnector
from app.validation import json_object, text

carrier_ns = Namespace('carriers', description='External carrier API connectors')


# ── Stored-SSRF guard ────────────────────────────────────────────────────────
#
# Every URL written here is one that a dispatcher, a webhook poster or a
# customer's browser will later fetch. An admin console that accepts
# ``http://127.0.0.1:6379/`` or ``http://169.254.169.254/`` stores a request
# forgery and fires it later with the server's own network position. Rejecting
# at write time is the only point where the value is still attributable to a
# person.

_BLOCKED_HOSTS = {
    'localhost', 'localhost.localdomain',
    'ip6-localhost', 'ip6-loopback',
    'metadata', 'metadata.google.internal',
}

_BLOCKED_SUFFIXES = ('.localhost', '.local', '.internal', '.lan', '.home.arpa')


def check_public_url(value):
    """``(ok, reason)`` for a URL an admin wants us to store and later fetch.

    Only the literal host is judged; no DNS lookup is made, because this code
    must not touch the network and a name can resolve differently later. The
    fetching layer is expected to re-check the resolved address when live
    calls are switched on.
    """
    if not isinstance(value, str) or not value.strip():
        return False, 'must be a URL'

    candidate = value.strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False, 'is not a valid URL'

    if parsed.scheme.lower() not in ('http', 'https'):
        return False, 'must use http or https'
    if '@' in (parsed.netloc or ''):
        return False, 'must not embed credentials in the URL'

    try:
        host = (parsed.hostname or '').strip().lower()
    except ValueError:
        return False, 'is not a valid URL'
    if not host:
        return False, 'must include a host'
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        return False, 'must not point at a loopback or internal host'

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
    ):
        return False, 'must not point at a private or loopback address'

    return True, None


def require_public_url(ns, field, value):
    """Abort 400 unless ``value`` is a URL we are willing to store."""
    ok, reason = check_public_url(value)
    if not ok:
        ns.abort(400, f"'{field}' {reason}")
    return value.strip()


# ── The outbound seam ────────────────────────────────────────────────────────

#: Flipped on once a dispatcher with retries, rate limiting and
#: resolved-address SSRF checks exists. Until then nothing leaves the process.
CARRIER_LIVE_CALLS_ENABLED = False


def call_carrier(connector, operation, payload=None):
    """SEAM: the one place an outbound carrier HTTP request will be made.

    Returns a "not enabled" result rather than a fabricated success, so a
    caller that forgets to check cannot mistake a no-op for a booking.
    """
    if not CARRIER_LIVE_CALLS_ENABLED:
        return {
            "ok": False,
            "enabled": False,
            "reason": "carrier_live_calls_not_enabled",
            "carrier": getattr(connector, 'code', None),
            "operation": operation,
        }
    raise NotImplementedError(
        "Live carrier dispatch is not implemented yet; see call_carrier()."
    )


# ── Writable field whitelist ─────────────────────────────────────────────────
#
# The model has ~70 columns, a handful of which are credentials and a handful
# of which are health counters the system owns. ``Model(**payload)`` or a
# blind setattr loop over the request body would let a caller set
# ``api_key_encrypted`` to chosen ciphertext, flip ``health_status``, or
# overwrite ``code``. Every writable field is named here instead.

_STRING_FIELDS = {
    "name": 120, "display_name": 120, "logo_url": 500, "website_url": 500,
    "instructions": 5000, "api_version": 30, "content_type": 80,
    "auth_header_name": 80, "auth_header_prefix": 40, "auth_query_param": 80,
    "account_number": 120, "username": 160, "client_id": 255,
    "oauth_scope": 255, "oauth_audience": 255,
    "track_endpoint": 300, "create_shipment_endpoint": 300, "label_endpoint": 300,
    "rate_endpoint": 300, "pickup_endpoint": 300, "cancel_endpoint": 300,
    "tracking_number_path": 200, "status_path": 200, "events_path": 200,
    "event_status_path": 200, "event_time_path": 200, "event_location_path": 200,
    "event_description_path": 200, "event_time_format": 60, "error_message_path": 200,
    "webhook_signature_header": 80, "webhook_signature_algo": 30,
}

#: Fetched by us or opened by a customer, so each goes through the SSRF guard.
_URL_FIELDS = (
    "api_base_url", "sandbox_base_url", "oauth_token_url",
    "webhook_url", "tracking_url_template",
)

_BOOL_FIELDS = (
    "is_active", "is_sandbox", "supports_tracking", "supports_booking",
    "supports_label", "supports_rates", "supports_pickup", "supports_cancel",
    "supports_webhook",
)

_INT_FIELDS = {
    "sort_order": (0, 100000), "timeout_seconds": (1, 300), "max_retries": (0, 20),
    "retry_backoff_seconds": (0, 3600), "rate_limit_per_minute": (1, 100000),
    "poll_interval_minutes": (1, 10080),
}

_DICT_FIELDS = (
    "extra_headers", "extra_query_params", "status_map",
    "track_request_template", "create_request_template",
)

_LIST_FIELDS = ("supported_countries", "supported_services")

#: Plaintext in, ciphertext at rest, via the EncryptedField descriptors.
_SECRET_FIELDS = ("api_key", "api_secret", "password", "client_secret", "webhook_secret")

_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _apply_carrier_fields(connector, data):
    """Copy the whitelisted fields of ``data`` onto ``connector``."""
    for field, limit in _STRING_FIELDS.items():
        if field in data:
            value = data[field]
            setattr(connector, field, None if value is None else text(value, field, limit))

    for field in _URL_FIELDS:
        if field in data:
            value = data[field]
            if value in (None, ""):
                setattr(connector, field, None)
            else:
                setattr(connector, field, require_public_url(carrier_ns, field, value))

    for field in _BOOL_FIELDS:
        if field in data:
            if not isinstance(data[field], bool):
                carrier_ns.abort(400, f"'{field}' must be true or false")
            setattr(connector, field, data[field])

    for field, (low, high) in _INT_FIELDS.items():
        if field in data:
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                carrier_ns.abort(400, f"'{field}' must be an integer between {low} and {high}")
            setattr(connector, field, value)

    for field in _DICT_FIELDS:
        if field in data:
            value = data[field]
            if value is None:
                setattr(connector, field, {})
            elif isinstance(value, dict):
                setattr(connector, field, value)
            else:
                carrier_ns.abort(400, f"'{field}' must be an object")

    for field in _LIST_FIELDS:
        if field in data:
            value = data[field]
            if value is None:
                setattr(connector, field, [])
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                setattr(connector, field, value)
            else:
                carrier_ns.abort(400, f"'{field}' must be a list of strings")

    for field in ("track_method", "create_method"):
        if field in data:
            value = data[field]
            if not isinstance(value, str) or value.upper() not in _HTTP_METHODS:
                carrier_ns.abort(400, f"'{field}' must be one of {', '.join(_HTTP_METHODS)}")
            setattr(connector, field, value.upper())

    if "auth_type" in data:
        if data["auth_type"] not in CarrierConnector.AUTH_TYPES:
            carrier_ns.abort(400, f"'auth_type' must be one of {', '.join(CarrierConnector.AUTH_TYPES)}")
        connector.auth_type = data["auth_type"]

    # Credentials last. An empty string clears the stored secret; omitting the
    # key leaves whatever is already encrypted in place, so a round-trip of the
    # masked admin form does not wipe the credential.
    for field in _SECRET_FIELDS:
        if field in data:
            value = data[field]
            if value in (None, ""):
                setattr(connector, f"{field}_encrypted", None)
            elif isinstance(value, str):
                setattr(connector, field, value)
            else:
                carrier_ns.abort(400, f"'{field}' must be a string")

    return connector


# ── Configuration audit (no network) ─────────────────────────────────────────

def _audit(connector):
    """What is still missing before this connector could be used."""
    problems = []

    if not connector.base_url:
        problems.append(
            "sandbox_base_url is not set and is_sandbox is on" if connector.is_sandbox
            else "api_base_url is not set"
        )

    auth = connector.auth_type
    if auth not in CarrierConnector.AUTH_TYPES:
        problems.append(f"auth_type '{auth}' is not recognised")
    elif auth in (CarrierConnector.AUTH_API_KEY, CarrierConnector.AUTH_BEARER):
        if not connector.api_key_encrypted:
            problems.append("api_key is required for this auth_type")
        if not connector.auth_header_name and not connector.auth_query_param:
            problems.append("auth_header_name or auth_query_param must say where the key goes")
    elif auth == CarrierConnector.AUTH_BASIC:
        if not connector.username:
            problems.append("username is required for basic auth")
        if not connector.password_encrypted:
            problems.append("password is required for basic auth")
    elif auth == CarrierConnector.AUTH_OAUTH2:
        if not connector.client_id:
            problems.append("client_id is required for oauth2")
        if not connector.client_secret_encrypted:
            problems.append("client_secret is required for oauth2")
        if not connector.oauth_token_url:
            problems.append("oauth_token_url is required for oauth2")
    elif auth == CarrierConnector.AUTH_HMAC:
        if not connector.api_key_encrypted:
            problems.append("api_key is required for hmac")
        if not connector.api_secret_encrypted:
            problems.append("api_secret is required for hmac")

    if connector.supports_tracking:
        if not connector.track_endpoint:
            problems.append("supports_tracking is on but track_endpoint is not set")
        if not connector.events_path:
            problems.append("supports_tracking is on but events_path is not set")
        if not connector.event_status_path:
            problems.append("supports_tracking is on but event_status_path is not set")
        if not (connector.status_map or {}):
            problems.append("status_map is empty, so carrier statuses cannot be translated")
    if connector.supports_booking and not connector.create_shipment_endpoint:
        problems.append("supports_booking is on but create_shipment_endpoint is not set")
    if connector.supports_label and not connector.label_endpoint:
        problems.append("supports_label is on but label_endpoint is not set")
    if connector.supports_rates and not connector.rate_endpoint:
        problems.append("supports_rates is on but rate_endpoint is not set")
    if connector.supports_pickup and not connector.pickup_endpoint:
        problems.append("supports_pickup is on but pickup_endpoint is not set")
    if connector.supports_cancel and not connector.cancel_endpoint:
        problems.append("supports_cancel is on but cancel_endpoint is not set")
    if connector.supports_webhook:
        if not connector.webhook_url:
            problems.append("supports_webhook is on but webhook_url is not set")
        if not connector.webhook_secret_encrypted:
            problems.append("supports_webhook is on but no webhook_secret is stored to verify signatures")
        if not connector.webhook_signature_header:
            problems.append("supports_webhook is on but webhook_signature_header is not set")

    if not connector.tracking_url_template:
        problems.append("tracking_url_template is not set, so customers get no public link")
    elif "{tracking_no}" not in connector.tracking_url_template:
        problems.append("tracking_url_template does not contain the {tracking_no} placeholder")

    return problems


# ── Swagger shapes (documentation only; validation is explicit above) ────────

carrier_input = carrier_ns.model("CarrierConnectorInput", {
    "code": fields.String(required=True, description="Stable short code, e.g. 'dhl'"),
    "name": fields.String(required=True),
    "display_name": fields.String(),
    "is_active": fields.Boolean(),
    "is_sandbox": fields.Boolean(),
    "auth_type": fields.String(enum=list(CarrierConnector.AUTH_TYPES)),
    "api_base_url": fields.String(description="Public http/https URL"),
    "sandbox_base_url": fields.String(),
    "tracking_url_template": fields.String(description="Must contain {tracking_no}"),
    "webhook_url": fields.String(),
    "api_key": fields.String(description="Write-only; only ever returned masked"),
    "api_secret": fields.String(description="Write-only"),
    "status_map": fields.Raw(description="Carrier status -> our status"),
})


# ── Routes ───────────────────────────────────────────────────────────────────

@carrier_ns.route("/")
class CarrierList(Resource):
    @carrier_ns.doc("list_carriers")
    @carrier_ns.response(200, "Active carriers, public fields only")
    def get(self):
        """List active carriers (public; no connection details)."""
        carriers = (CarrierConnector.query
                    .filter_by(is_active=True)
                    .order_by(CarrierConnector.sort_order, CarrierConnector.name)
                    .all())
        return [c.to_dict() for c in carriers]

    @carrier_ns.doc("create_carrier", security="Bearer")
    @carrier_ns.expect(carrier_input)
    @carrier_ns.response(201, "Created")
    @carrier_ns.response(400, "Validation error")
    @carrier_ns.response(409, "Code already exists")
    @require_roles("super_admin")
    def post(self):
        """Create a carrier connector (super_admin)."""
        data = json_object()
        code = text(data.get("code"), "code", 40, required=True).lower()
        if not data.get("name"):
            carrier_ns.abort(400, "'name' is required")
        if CarrierConnector.query.filter_by(code=code).first():
            carrier_ns.abort(409, "Carrier code already exists")

        connector = CarrierConnector(code=code, name=text(data["name"], "name", 120, required=True))
        _apply_carrier_fields(connector, data)
        db.session.add(connector)
        db.session.commit()
        return connector.to_dict(include_config=True, include_secrets=True), 201


@carrier_ns.route("/all")
class CarrierListAdmin(Resource):
    @carrier_ns.doc("list_carriers_admin", security="Bearer")
    @carrier_ns.response(200, "All carriers with configuration and masked secrets")
    @require_roles("super_admin")
    def get(self):
        """List every carrier with its configuration (super_admin)."""
        carriers = CarrierConnector.query.order_by(CarrierConnector.sort_order,
                                                   CarrierConnector.name).all()
        return [c.to_dict(include_config=True, include_secrets=True) for c in carriers]


@carrier_ns.route("/<string:code>")
@carrier_ns.response(404, "Carrier not found")
@carrier_ns.param("code", "The carrier short code")
class CarrierDetail(Resource):
    @carrier_ns.doc("get_carrier")
    def get(self, code):
        """Fetch a carrier by code (public; no connection details)."""
        connector = CarrierConnector.query.filter_by(code=code).first()
        if not connector or not connector.is_active:
            carrier_ns.abort(404, "Carrier not found")
        return connector.to_dict()

    @carrier_ns.doc("update_carrier", security="Bearer")
    @carrier_ns.expect(carrier_input)
    @carrier_ns.response(400, "Validation error")
    @require_roles("super_admin")
    def put(self, code):
        """Update a carrier connector (super_admin)."""
        connector = CarrierConnector.query.filter_by(code=code).first()
        if not connector:
            carrier_ns.abort(404, "Carrier not found")
        # 'code' is the join key used by shipments; changing it would orphan
        # rows, so it is not in the writable set.
        _apply_carrier_fields(connector, json_object())
        db.session.commit()
        return connector.to_dict(include_config=True, include_secrets=True)

    @carrier_ns.doc("deactivate_carrier", security="Bearer")
    @require_roles("super_admin")
    def delete(self, code):
        """Deactivate a carrier (super_admin). Rows are kept for history."""
        connector = CarrierConnector.query.filter_by(code=code).first()
        if not connector:
            carrier_ns.abort(404, "Carrier not found")
        connector.is_active = False
        db.session.commit()
        return {"msg": f"Carrier {code} deactivated"}


@carrier_ns.route("/<string:code>/test")
@carrier_ns.response(404, "Carrier not found")
@carrier_ns.param("code", "The carrier short code")
class CarrierTest(Resource):
    @carrier_ns.doc("test_carrier", security="Bearer")
    @carrier_ns.response(200, "Configuration audit")
    @require_roles("super_admin")
    def post(self, code):
        """Audit a connector's configuration (super_admin).

        Reports what is missing. Makes **no** network call -- see
        ``call_carrier`` for where live dispatch will live.
        """
        connector = CarrierConnector.query.filter_by(code=code).first()
        if not connector:
            carrier_ns.abort(404, "Carrier not found")

        problems = _audit(connector)
        attempted = call_carrier(connector, "ping")
        return {
            "code": connector.code,
            "is_configured": connector.is_configured,
            "ready": not problems,
            "missing": problems,
            "base_url": connector.base_url,
            "environment": "sandbox" if connector.is_sandbox else "live",
            "network_call_made": False,
            "live_dispatch": attempted,
            "checked_at": datetime.utcnow().isoformat(),
        }


@carrier_ns.route("/<string:code>/tracking-url/<string:tracking_no>")
@carrier_ns.response(404, "Carrier not found or no tracking URL configured")
@carrier_ns.param("code", "The carrier short code")
@carrier_ns.param("tracking_no", "The carrier's own tracking number")
class CarrierTrackingUrl(Resource):
    @carrier_ns.doc("carrier_tracking_url")
    def get(self, code, tracking_no):
        """The public URL a customer can open for this tracking number."""
        connector = CarrierConnector.query.filter_by(code=code).first()
        if not connector or not connector.is_active:
            carrier_ns.abort(404, "Carrier not found")
        url = connector.public_tracking_url(tracking_no)
        if not url:
            carrier_ns.abort(404, "This carrier has no public tracking URL configured")
        return {"code": connector.code, "tracking_no": tracking_no, "tracking_url": url}
