"""Admin console over the legacy :class:`TrackingProvider` record.

Superseded by ``app/carriers`` but still in service, so it gets the same
three guarantees that module already has: the API key is encrypted at rest
and only ever returned masked, every admin-settable URL goes through the
shared stored-SSRF guard at write time, and the request body is copied onto
the row field by field instead of being splatted over the model.
"""

import re

from flask_restx import Namespace, Resource, fields

from app.auth.utils import require_roles
from app.carriers.routes import require_public_url
from app.extensions import db
from app.models.tracking_provider import TrackingProvider
from app.validation import json_object, text

tp_ns = Namespace("tracking-providers", description="Courier tracking provider management")

#: ``api_url_template`` carries ``{tracking_no}``; a brace placeholder is not
#: valid URL syntax, so it is swapped for a harmless token before parsing.
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_PLACEHOLDER_PROBE = "placeholder"


# ── Writable field whitelist ─────────────────────────────────────────────────
#
# ``TrackingProvider(**data)`` and a setattr loop guarded only by ``hasattr``
# let a caller write ``id``, ``created_at`` or even ``to_dict``. Every field a
# request may set is named here, with its coercion and bound.

_STRING_FIELDS = {"name": 100, "instructions": 5000, "supported_countries": 500}

#: Stored for later fetching or for a customer's browser to open.
_URL_FIELDS = ("api_url_template", "webhook_url", "logo_url")

_BOOL_FIELDS = ("is_active",)

_SECRET_FIELDS = ("api_key",)

#: ``code`` is the lookup key for every route here, so it is settable at
#: creation and refused afterwards rather than silently ignored.
_WRITABLE = set(_STRING_FIELDS) | set(_URL_FIELDS) | set(_BOOL_FIELDS) | set(_SECRET_FIELDS)


def _reject_unknown(data, creating):
    """400 on any key the caller is not allowed to write."""
    allowed = _WRITABLE | ({"code"} if creating else set())
    for key in data:
        if key not in allowed:
            if key == "code":
                tp_ns.abort(400, "'code' cannot be changed once the provider exists")
            tp_ns.abort(400, f"'{key}' is not a writable field")


def _check_url(field, value):
    """Validate a provider URL and return it as it should be stored."""
    candidate = text(value, field, 500, required=True)
    # Validate a placeholder-free copy, then store the caller's own template.
    require_public_url(tp_ns, field, _PLACEHOLDER.sub(_PLACEHOLDER_PROBE, candidate))
    return candidate


def _apply_provider_fields(provider, data):
    """Copy the whitelisted fields of ``data`` onto ``provider``."""
    for field, limit in _STRING_FIELDS.items():
        if field in data:
            value = data[field]
            setattr(provider, field, None if value is None else text(value, field, limit))

    for field in _URL_FIELDS:
        if field in data:
            value = data[field]
            setattr(provider, field, None if value in (None, "") else _check_url(field, value))

    for field in _BOOL_FIELDS:
        if field in data:
            if not isinstance(data[field], bool):
                tp_ns.abort(400, f"'{field}' must be true or false")
            setattr(provider, field, data[field])

    # Credentials last. An empty string clears the stored secret; omitting the
    # key leaves the ciphertext in place, so an admin form that round-trips the
    # masked value cannot overwrite the real credential with the mask.
    for field in _SECRET_FIELDS:
        if field in data:
            value = data[field]
            if value in (None, ""):
                setattr(provider, f"{field}_encrypted", None)
            elif isinstance(value, str):
                setattr(provider, field, value)
            else:
                tp_ns.abort(400, f"'{field}' must be a string")

    return provider


# ── Swagger shapes (documentation only; validation is explicit above) ────────

provider_public = tp_ns.model("ProviderPublic", {
    "id": fields.Integer(), "name": fields.String(),
    "code": fields.String(), "logo_url": fields.String(),
    "instructions": fields.String(), "is_active": fields.Boolean(),
    "supported_countries": fields.String(),
})
provider_admin = tp_ns.model("ProviderAdmin", {
    "id": fields.Integer(), "name": fields.String(), "code": fields.String(),
    "api_url_template": fields.String(), "api_key_masked": fields.String(),
    "webhook_url": fields.String(), "logo_url": fields.String(),
    "instructions": fields.String(), "is_active": fields.Boolean(),
    "supported_countries": fields.String(),
})
provider_input = tp_ns.model("ProviderInput", {
    "name": fields.String(required=True), "code": fields.String(required=True),
    "api_url_template": fields.String(description="Public http/https URL; may contain {tracking_no}"),
    "api_key": fields.String(description="Write-only; only ever returned masked"),
    "webhook_url": fields.String(), "logo_url": fields.String(),
    "instructions": fields.String(), "is_active": fields.Boolean(),
    "supported_countries": fields.String(),
})


@tp_ns.route("/")
class ProviderList(Resource):
    @tp_ns.response(200, "Active providers, public fields only", [provider_public])
    def get(self):
        """List all active tracking providers (public)."""
        providers = TrackingProvider.query.filter_by(is_active=True).all()
        return [p.to_dict() for p in providers]

    @tp_ns.doc(security="Bearer")
    @tp_ns.expect(provider_input)
    @tp_ns.response(400, "Validation error")
    @tp_ns.response(409, "Code already exists")
    @require_roles("super_admin")
    def post(self):
        """Create a tracking provider (super_admin)."""
        data = json_object()
        _reject_unknown(data, creating=True)
        code = text(data.get("code"), "code", 30, required=True)
        if TrackingProvider.query.filter_by(code=code).first():
            return {"msg": "Provider code already exists"}, 409

        provider = TrackingProvider(code=code, name=text(data.get("name"), "name", 100, required=True))
        _apply_provider_fields(provider, data)
        db.session.add(provider)
        db.session.commit()
        return provider.to_dict(include_secrets=True), 201


@tp_ns.route("/all")
class ProviderListAdmin(Resource):
    @tp_ns.doc(security="Bearer")
    @tp_ns.response(200, "All providers, with masked secrets", [provider_admin])
    @require_roles("super_admin")
    def get(self):
        """List ALL providers including inactive (super_admin)."""
        return [p.to_dict(include_secrets=True) for p in TrackingProvider.query.all()]


@tp_ns.route("/<string:code>")
class ProviderDetail(Resource):
    def get(self, code):
        """Get provider by code (public, no secrets)."""
        p = TrackingProvider.query.filter_by(code=code).first_or_404()
        return p.to_dict()

    @tp_ns.doc(security="Bearer")
    @tp_ns.expect(provider_input)
    @tp_ns.response(400, "Validation error")
    @require_roles("super_admin")
    def put(self, code):
        """Update provider (super_admin)."""
        p = TrackingProvider.query.filter_by(code=code).first_or_404()
        data = json_object()
        _reject_unknown(data, creating=False)
        _apply_provider_fields(p, data)
        db.session.commit()
        return p.to_dict(include_secrets=True)

    @tp_ns.doc(security="Bearer")
    @require_roles("super_admin")
    def delete(self, code):
        """Deactivate provider (super_admin)."""
        p = TrackingProvider.query.filter_by(code=code).first_or_404()
        p.is_active = False
        db.session.commit()
        return {"msg": f"Provider {code} deactivated"}
