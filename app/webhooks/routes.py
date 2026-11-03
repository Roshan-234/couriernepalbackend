"""Outbound webhook endpoints and deliveries.

A merchant who wants to know when a parcel moves currently polls the
tracking endpoint, which is how integrations get rate-limited into
uselessness. Registering an endpoint here means we push instead.

What is real today: the endpoint registry, the signing secret, and the
delivery queue. ``enqueue_event`` writes a ``WebhookDelivery`` row in
``pending`` for every active endpoint subscribed to the event -- that is the
durable part, and it is what other modules should call. ``post_delivery`` is
the seam where the actual HTTP POST will go; it currently reports that
sending is disabled rather than marking a row delivered that never left.
"""

from datetime import datetime

from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.carriers.routes import require_public_url
from app.extensions import db
from app.models.webhook import WebhookEndpoint, WebhookDelivery
from app.validation import json_object, pagination, text

webhook_ns = Namespace('webhooks', description='Outbound webhook endpoints and deliveries')


# ── The outbound seam ────────────────────────────────────────────────────────

#: Flipped on once a worker exists to drain the queue with retries and
#: backoff. Until then rows accumulate in ``pending`` and nothing is sent.
WEBHOOK_SENDING_ENABLED = False


def post_delivery(delivery):
    """SEAM: the one place a webhook HTTP POST will be made.

    Returns a "not enabled" result instead of a fabricated 200, so the
    delivery row is never advanced past ``pending`` by accident.
    """
    if not WEBHOOK_SENDING_ENABLED:
        return {
            "ok": False,
            "enabled": False,
            "reason": "webhook_sending_not_enabled",
            "delivery_id": getattr(delivery, 'id', None),
        }
    raise NotImplementedError(
        "Webhook dispatch is not implemented yet; see post_delivery()."
    )


# ── The internal helper other modules call ───────────────────────────────────

def enqueue_event(event_type, payload, merchant_id=None, commit=False):
    """Queue ``event_type`` for every active endpoint subscribed to it.

    This is the real, testable half of the webhook story: one ``pending``
    :class:`WebhookDelivery` row per matching endpoint, carrying the payload
    as sent. A worker will later pick them up through ``post_delivery``.

    ``merchant_id`` narrows delivery to one merchant's endpoints, which is
    almost always what a caller wants -- an event about one merchant's
    shipment must not reach another's URL. Rows are flushed so ids are
    available; set ``commit`` when there is no surrounding transaction.
    """
    query = WebhookEndpoint.query.filter_by(is_active=True)
    if merchant_id is not None:
        query = query.filter_by(merchant_id=merchant_id)

    queued = []
    for endpoint in query.all():
        if not endpoint.subscribes_to(event_type):
            continue
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event_type=event_type,
            event_id=WebhookDelivery.generate_event_id(),
            payload=payload,
            status=WebhookDelivery.PENDING,
            attempt_count=0,
        )
        db.session.add(delivery)
        queued.append(delivery)

    if queued:
        db.session.flush()
    if commit:
        db.session.commit()
    return queued


# ── Field whitelist ──────────────────────────────────────────────────────────

def _validate_events(events):
    if not isinstance(events, list) or not events:
        webhook_ns.abort(400, "'events' must be a non-empty list")
    for event in events:
        if event != '*' and event not in WebhookEndpoint.EVENTS:
            webhook_ns.abort(400, f"Unknown event '{event}'")
    return events


def _apply_endpoint_fields(endpoint, data):
    """Copy only the fields a merchant is allowed to set.

    Failure counters, ``disabled_at`` and the secret ciphertext belong to the
    system; a blind setattr loop would hand them to the caller.
    """
    if "url" in data:
        endpoint.url = require_public_url(webhook_ns, "url", data["url"])
    if "description" in data:
        value = data["description"]
        endpoint.description = None if value is None else text(value, "description", 255)
    if "events" in data:
        endpoint.events = _validate_events(data["events"])
    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            webhook_ns.abort(400, "'is_active' must be true or false")
        endpoint.is_active = data["is_active"]
    for field, low, high in (("timeout_seconds", 1, 120), ("max_retries", 0, 20)):
        if field in data:
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                webhook_ns.abort(400, f"'{field}' must be an integer between {low} and {high}")
            setattr(endpoint, field, value)
    if "signature_header" in data:
        endpoint.signature_header = text(data["signature_header"], "signature_header", 80,
                                         required=True)
    return endpoint


def _own_endpoint(endpoint_id, user_id):
    endpoint = db.session.get(WebhookEndpoint, endpoint_id)
    if not endpoint or endpoint.merchant_id != user_id:
        # 404 rather than 403: another merchant's endpoint id should not be
        # confirmable from the outside.
        webhook_ns.abort(404, "Webhook endpoint not found")
    return endpoint


# ── Swagger shapes ───────────────────────────────────────────────────────────

endpoint_input = webhook_ns.model("WebhookEndpointInput", {
    "url": fields.String(required=True, description="Public https URL to POST to"),
    "events": fields.List(fields.String, required=True,
                          description=f"Any of: {', '.join(WebhookEndpoint.EVENTS)}, or '*'"),
    "description": fields.String(),
    "is_active": fields.Boolean(),
    "timeout_seconds": fields.Integer(),
    "max_retries": fields.Integer(),
})


# ── Routes ───────────────────────────────────────────────────────────────────

@webhook_ns.route("/")
class EndpointList(Resource):
    @webhook_ns.doc("list_webhook_endpoints", security="Bearer")
    @webhook_ns.response(200, "Your webhook endpoints")
    @jwt_required()
    def get(self):
        """List your own webhook endpoints."""
        uid = int(get_jwt_identity())
        endpoints = (WebhookEndpoint.query
                     .filter_by(merchant_id=uid)
                     .order_by(WebhookEndpoint.created_at.desc())
                     .all())
        return [e.to_dict(include_secret_mask=True) for e in endpoints]

    @webhook_ns.doc("create_webhook_endpoint", security="Bearer")
    @webhook_ns.expect(endpoint_input)
    @webhook_ns.response(201, "Created; the signing secret is returned once")
    @webhook_ns.response(400, "Validation error")
    @jwt_required()
    def post(self):
        """Register a webhook endpoint. The signing secret is shown once."""
        uid = int(get_jwt_identity())
        data = json_object()
        if "url" not in data:
            webhook_ns.abort(400, "'url' is required")
        if "events" not in data:
            webhook_ns.abort(400, "'events' is required")

        endpoint = WebhookEndpoint(merchant_id=uid)
        _apply_endpoint_fields(endpoint, data)
        secret = WebhookEndpoint.generate_secret()
        endpoint.secret = secret
        db.session.add(endpoint)
        db.session.commit()

        body = endpoint.to_dict(include_secret_mask=True)
        body["secret"] = secret
        body["secret_notice"] = (
            "Store this signing secret now. It is shown only once and cannot be retrieved later."
        )
        return body, 201


@webhook_ns.route("/<int:endpoint_id>")
@webhook_ns.response(404, "Webhook endpoint not found")
@webhook_ns.param("endpoint_id", "The endpoint identifier")
class EndpointDetail(Resource):
    @webhook_ns.doc("get_webhook_endpoint", security="Bearer")
    @jwt_required()
    def get(self, endpoint_id):
        """Fetch one of your webhook endpoints."""
        endpoint = _own_endpoint(endpoint_id, int(get_jwt_identity()))
        return endpoint.to_dict(include_secret_mask=True)

    @webhook_ns.doc("update_webhook_endpoint", security="Bearer")
    @webhook_ns.expect(endpoint_input)
    @jwt_required()
    def put(self, endpoint_id):
        """Update one of your webhook endpoints."""
        endpoint = _own_endpoint(endpoint_id, int(get_jwt_identity()))
        _apply_endpoint_fields(endpoint, json_object())
        db.session.commit()
        return endpoint.to_dict(include_secret_mask=True)

    @webhook_ns.doc("delete_webhook_endpoint", security="Bearer")
    @jwt_required()
    def delete(self, endpoint_id):
        """Delete one of your webhook endpoints and its delivery history."""
        endpoint = _own_endpoint(endpoint_id, int(get_jwt_identity()))
        db.session.delete(endpoint)
        db.session.commit()
        return {"msg": "Webhook endpoint deleted"}


@webhook_ns.route("/<int:endpoint_id>/deliveries")
@webhook_ns.response(404, "Webhook endpoint not found")
@webhook_ns.param("endpoint_id", "The endpoint identifier")
class EndpointDeliveries(Resource):
    @webhook_ns.doc("list_webhook_deliveries", security="Bearer")
    @jwt_required()
    def get(self, endpoint_id):
        """List delivery attempts for one of your endpoints (paginated)."""
        endpoint = _own_endpoint(endpoint_id, int(get_jwt_identity()))
        page, per_page = pagination()
        query = (WebhookDelivery.query
                 .filter_by(endpoint_id=endpoint.id)
                 .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()))
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        return {
            "deliveries": [d.to_dict(include_payload=True) for d in paginated.items],
            "total": paginated.total,
            "page": paginated.page,
            "pages": paginated.pages,
            "per_page": per_page,
        }


@webhook_ns.route("/<int:endpoint_id>/test")
@webhook_ns.response(404, "Webhook endpoint not found")
@webhook_ns.param("endpoint_id", "The endpoint identifier")
class EndpointTest(Resource):
    @webhook_ns.doc("test_webhook_endpoint", security="Bearer")
    @webhook_ns.response(202, "A test delivery was queued")
    @jwt_required()
    def post(self, endpoint_id):
        """Queue a test delivery for one of your endpoints.

        The row is written in ``pending``. Nothing is sent until the
        dispatcher behind ``post_delivery`` is switched on.
        """
        endpoint = _own_endpoint(endpoint_id, int(get_jwt_identity()))
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event_type="webhook.test",
            event_id=WebhookDelivery.generate_event_id(),
            payload={
                "event": "webhook.test",
                "endpoint_id": endpoint.id,
                "message": "Test event queued from the Moonlight dashboard.",
                "created_at": datetime.utcnow().isoformat(),
            },
            status=WebhookDelivery.PENDING,
            attempt_count=0,
        )
        db.session.add(delivery)
        db.session.commit()
        return {
            "msg": "Test delivery queued",
            "delivery": delivery.to_dict(include_payload=True),
            "sending_enabled": WEBHOOK_SENDING_ENABLED,
        }, 202
