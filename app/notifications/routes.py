"""Notification templates, SMS gateway configuration and the send log.

Customer wording used to live in the code -- present for the password reset
and absent everywhere else -- so changing an SMS meant a deploy. Templates
are rows here, each with an English and a Nepali body, and operations edits
them directly.

``send_sms`` is the seam where a gateway call will go. Nothing is sent from
this module today: a provider can be configured and a template can be
previewed, but no message leaves the process.
"""

from flask_restx import Namespace, Resource, fields

from app.auth.utils import require_roles
from app.carriers.routes import require_public_url
from app.extensions import db
from app.models.notification import NotificationLog
from app.models.notification_template import NotificationTemplate, SmsProvider
from app.validation import json_object, pagination, text

notification_ns = Namespace('notifications', description='Notification templates and dispatch')


# ── The outbound seam ────────────────────────────────────────────────────────

#: Flipped on once an aggregator contract is live and a queue exists.
SMS_SENDING_ENABLED = False


def send_sms(provider, recipient, message):
    """SEAM: the one place an SMS gateway request will be made.

    Returns a "not enabled" result rather than a fabricated success, so a
    caller cannot log a message as sent that was never handed to a gateway.
    """
    if not SMS_SENDING_ENABLED:
        return {
            "ok": False,
            "enabled": False,
            "reason": "sms_sending_not_enabled",
            "provider": getattr(provider, 'code', None),
        }
    raise NotImplementedError(
        "SMS dispatch is not implemented yet; see send_sms()."
    )


# ── Templates ────────────────────────────────────────────────────────────────

template_input = notification_ns.model("NotificationTemplateInput", {
    "code": fields.String(required=True, description="Stable lookup code"),
    "name": fields.String(required=True),
    "event": fields.String(required=True,
                           enum=list(NotificationTemplate.EVENTS)),
    "channel": fields.String(required=True, enum=list(NotificationTemplate.CHANNELS)),
    "subject": fields.String(),
    "body_en": fields.String(description="Uses {placeholder} names from 'variables'"),
    "body_ne": fields.String(description="Nepali variant"),
    "variables": fields.List(fields.String),
    "is_active": fields.Boolean(),
    "send_to_sender": fields.Boolean(),
    "send_to_receiver": fields.Boolean(),
})

preview_input = notification_ns.model("NotificationPreviewInput", {
    "context": fields.Raw(description="Placeholder values to substitute"),
    "language": fields.String(enum=["en", "ne"], default="en"),
})

_TEMPLATE_STRINGS = {"name": 150, "subject": 255, "body_en": 10000, "body_ne": 10000}
_TEMPLATE_BOOLS = ("is_active", "send_to_sender", "send_to_receiver")


def _apply_template_fields(template, data):
    """Copy only the editable fields. ``code`` is the lookup key, not editable."""
    for field, limit in _TEMPLATE_STRINGS.items():
        if field in data:
            value = data[field]
            setattr(template, field, None if value is None else text(value, field, limit))
    if "event" in data:
        if data["event"] not in NotificationTemplate.EVENTS:
            notification_ns.abort(400, f"'event' must be one of "
                                       f"{', '.join(NotificationTemplate.EVENTS)}")
        template.event = data["event"]
    if "channel" in data:
        if data["channel"] not in NotificationTemplate.CHANNELS:
            notification_ns.abort(400, f"'channel' must be one of "
                                       f"{', '.join(NotificationTemplate.CHANNELS)}")
        template.channel = data["channel"]
    if "variables" in data:
        value = data["variables"]
        if value is None:
            template.variables = []
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            template.variables = value
        else:
            notification_ns.abort(400, "'variables' must be a list of strings")
    for field in _TEMPLATE_BOOLS:
        if field in data:
            if not isinstance(data[field], bool):
                notification_ns.abort(400, f"'{field}' must be true or false")
            setattr(template, field, data[field])
    return template


@notification_ns.route("/templates")
class TemplateList(Resource):
    @notification_ns.doc("list_templates", security="Bearer")
    @notification_ns.response(200, "Templates")
    @require_roles("admin", "super_admin")
    def get(self):
        """List notification templates (admin)."""
        templates = NotificationTemplate.query.order_by(NotificationTemplate.code).all()
        return [t.to_dict() for t in templates]

    @notification_ns.doc("create_template", security="Bearer")
    @notification_ns.expect(template_input)
    @notification_ns.response(201, "Created")
    @notification_ns.response(409, "Code already exists")
    @require_roles("admin", "super_admin")
    def post(self):
        """Create a notification template (admin)."""
        data = json_object()
        code = text(data.get("code"), "code", 60, required=True)
        for field in ("name", "event", "channel"):
            if not data.get(field):
                notification_ns.abort(400, f"'{field}' is required")
        if NotificationTemplate.query.filter_by(code=code).first():
            notification_ns.abort(409, "Template code already exists")

        template = NotificationTemplate(code=code, name=text(data["name"], "name", 150, required=True),
                                        event=data["event"], channel=data["channel"])
        _apply_template_fields(template, data)
        db.session.add(template)
        db.session.commit()
        return template.to_dict(), 201


@notification_ns.route("/templates/<string:code>")
@notification_ns.response(404, "Template not found")
@notification_ns.param("code", "The template code")
class TemplateDetail(Resource):
    @notification_ns.doc("get_template", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, code):
        """Fetch a template by code (admin)."""
        template = NotificationTemplate.query.filter_by(code=code).first()
        if not template:
            notification_ns.abort(404, "Template not found")
        return template.to_dict()

    @notification_ns.doc("update_template", security="Bearer")
    @notification_ns.expect(template_input)
    @require_roles("admin", "super_admin")
    def put(self, code):
        """Update a template (admin)."""
        template = NotificationTemplate.query.filter_by(code=code).first()
        if not template:
            notification_ns.abort(404, "Template not found")
        _apply_template_fields(template, json_object())
        db.session.commit()
        return template.to_dict()

    @notification_ns.doc("deactivate_template", security="Bearer")
    @require_roles("admin", "super_admin")
    def delete(self, code):
        """Deactivate a template (admin). Kept so old sends stay explicable."""
        template = NotificationTemplate.query.filter_by(code=code).first()
        if not template:
            notification_ns.abort(404, "Template not found")
        template.is_active = False
        db.session.commit()
        return {"msg": f"Template {code} deactivated"}


@notification_ns.route("/templates/<string:code>/preview")
@notification_ns.response(404, "Template not found")
@notification_ns.param("code", "The template code")
class TemplatePreview(Resource):
    @notification_ns.doc("preview_template", security="Bearer")
    @notification_ns.expect(preview_input)
    @notification_ns.response(200, "Rendered subject and body")
    @require_roles("admin", "super_admin")
    def post(self, code):
        """Render a template against sample context in ``en`` or ``ne``.

        Nothing is sent; this only exercises ``NotificationTemplate.render``.
        """
        template = NotificationTemplate.query.filter_by(code=code).first()
        if not template:
            notification_ns.abort(404, "Template not found")

        data = json_object()
        language = data.get("language", "en")
        if language not in ("en", "ne"):
            notification_ns.abort(400, "'language' must be 'en' or 'ne'")
        context = data.get("context", {})
        if context is None:
            context = {}
        if not isinstance(context, dict):
            notification_ns.abort(400, "'context' must be an object")

        subject, body = template.render(context, language)
        # A placeholder the caller did not supply survives rendering verbatim,
        # so say which ones are still unfilled rather than shipping "{otp}".
        unfilled = [v for v in (template.variables or []) if '{' + v + '}' in body]
        return {
            "code": template.code,
            "channel": template.channel,
            "language": language,
            "subject": subject,
            "body": body,
            "unfilled_variables": unfilled,
            "sent": False,
        }


# ── SMS providers ────────────────────────────────────────────────────────────

provider_input = notification_ns.model("SmsProviderInput", {
    "code": fields.String(required=True, description=f"e.g. {', '.join(SmsProvider.KNOWN_CODES)}"),
    "name": fields.String(required=True),
    "api_base_url": fields.String(description="Public http/https URL"),
    "send_endpoint": fields.String(),
    "token": fields.String(description="Write-only; only ever returned masked"),
    "secret": fields.String(description="Write-only"),
    "sender_id": fields.String(),
    "param_map": fields.Raw(description="Our field names -> the provider's"),
    "is_active": fields.Boolean(),
    "is_primary": fields.Boolean(),
})

_PROVIDER_STRINGS = {
    "name": 120, "send_endpoint": 300, "balance_endpoint": 300,
    "http_method": 10, "content_type": 80, "sender_id": 40, "account_id": 120,
}
_PROVIDER_BOOLS = ("is_active", "is_sandbox", "is_primary", "unicode_supported")
_PROVIDER_INTS = {"max_length": (1, 2000), "timeout_seconds": (1, 300),
                  "rate_limit_per_minute": (1, 100000)}
_PROVIDER_DICTS = ("param_map", "extra_params")
_PROVIDER_SECRETS = ("token", "secret")


def _apply_provider_fields(provider, data):
    """Copy only the writable provider fields; credentials via the descriptors."""
    for field, limit in _PROVIDER_STRINGS.items():
        if field in data:
            value = data[field]
            setattr(provider, field, None if value is None else text(value, field, limit))
    if "api_base_url" in data:
        value = data["api_base_url"]
        if value in (None, ""):
            provider.api_base_url = None
        else:
            provider.api_base_url = require_public_url(notification_ns, "api_base_url", value)
    for field in _PROVIDER_BOOLS:
        if field in data:
            if not isinstance(data[field], bool):
                notification_ns.abort(400, f"'{field}' must be true or false")
            setattr(provider, field, data[field])
    for field, (low, high) in _PROVIDER_INTS.items():
        if field in data:
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                notification_ns.abort(400, f"'{field}' must be an integer between {low} and {high}")
            setattr(provider, field, value)
    if "cost_per_sms" in data:
        value = data["cost_per_sms"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            notification_ns.abort(400, "'cost_per_sms' must be a non-negative number")
        provider.cost_per_sms = float(value)
    for field in _PROVIDER_DICTS:
        if field in data:
            value = data[field]
            if value is None:
                setattr(provider, field, {})
            elif isinstance(value, dict):
                setattr(provider, field, value)
            else:
                notification_ns.abort(400, f"'{field}' must be an object")
    for field in _PROVIDER_SECRETS:
        if field in data:
            value = data[field]
            if value in (None, ""):
                setattr(provider, f"{field}_encrypted", None)
            elif isinstance(value, str):
                setattr(provider, field, value)
            else:
                notification_ns.abort(400, f"'{field}' must be a string")
    return provider


@notification_ns.route("/providers")
class ProviderList(Resource):
    @notification_ns.doc("list_sms_providers", security="Bearer")
    @require_roles("super_admin")
    def get(self):
        """List SMS providers with masked credentials (super_admin)."""
        providers = SmsProvider.query.order_by(SmsProvider.code).all()
        return [p.to_dict(include_config=True) for p in providers]

    @notification_ns.doc("create_sms_provider", security="Bearer")
    @notification_ns.expect(provider_input)
    @notification_ns.response(201, "Created")
    @notification_ns.response(409, "Code already exists")
    @require_roles("super_admin")
    def post(self):
        """Configure an SMS provider (super_admin)."""
        data = json_object()
        code = text(data.get("code"), "code", 40, required=True).lower()
        if not data.get("name"):
            notification_ns.abort(400, "'name' is required")
        if SmsProvider.query.filter_by(code=code).first():
            notification_ns.abort(409, "Provider code already exists")

        provider = SmsProvider(code=code, name=text(data["name"], "name", 120, required=True))
        _apply_provider_fields(provider, data)
        db.session.add(provider)
        db.session.commit()
        return provider.to_dict(include_config=True), 201


@notification_ns.route("/providers/<string:code>")
@notification_ns.response(404, "Provider not found")
@notification_ns.param("code", "The provider code")
class ProviderDetail(Resource):
    @notification_ns.doc("get_sms_provider", security="Bearer")
    @require_roles("super_admin")
    def get(self, code):
        """Fetch one SMS provider with masked credentials (super_admin)."""
        provider = SmsProvider.query.filter_by(code=code).first()
        if not provider:
            notification_ns.abort(404, "Provider not found")
        return provider.to_dict(include_config=True)

    @notification_ns.doc("update_sms_provider", security="Bearer")
    @notification_ns.expect(provider_input)
    @require_roles("super_admin")
    def put(self, code):
        """Update an SMS provider (super_admin)."""
        provider = SmsProvider.query.filter_by(code=code).first()
        if not provider:
            notification_ns.abort(404, "Provider not found")
        _apply_provider_fields(provider, json_object())
        db.session.commit()
        return provider.to_dict(include_config=True)

    @notification_ns.doc("deactivate_sms_provider", security="Bearer")
    @require_roles("super_admin")
    def delete(self, code):
        """Deactivate an SMS provider (super_admin)."""
        provider = SmsProvider.query.filter_by(code=code).first()
        if not provider:
            notification_ns.abort(404, "Provider not found")
        provider.is_active = False
        provider.is_primary = False
        db.session.commit()
        return {"msg": f"Provider {code} deactivated"}


# ── Send log ─────────────────────────────────────────────────────────────────

@notification_ns.route("/logs")
class NotificationLogs(Resource):
    @notification_ns.doc("list_notification_logs", security="Bearer")
    @notification_ns.response(200, "Paginated send log")
    @require_roles("admin", "super_admin")
    def get(self):
        """Read the notification send log (admin, paginated).

        Filters: ``user_id``, ``channel``, ``status``.
        """
        from flask import request

        page, per_page = pagination()
        query = NotificationLog.query
        if request.args.get("user_id"):
            try:
                query = query.filter_by(user_id=int(request.args["user_id"]))
            except ValueError:
                notification_ns.abort(400, "'user_id' must be an integer")
        if request.args.get("channel"):
            query = query.filter_by(channel=request.args["channel"])
        if request.args.get("status"):
            query = query.filter_by(status=request.args["status"])

        paginated = (query.order_by(NotificationLog.sent_at.desc(), NotificationLog.id.desc())
                     .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "logs": [{
                "id": log.id,
                "user_id": log.user_id,
                "channel": log.channel,
                "payload": log.payload,
                "status": log.status,
                "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            } for log in paginated.items],
            "total": paginated.total,
            "page": paginated.page,
            "pages": paginated.pages,
            "per_page": per_page,
        }
