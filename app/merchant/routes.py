"""Merchant API keys and the key-authenticated shipping API.

Two audiences share this namespace. ``/keys`` is for a signed-in merchant
managing credentials in the dashboard, so it is JWT-gated. Everything below
it is the server-to-server API those credentials unlock, so it is gated by
``@require_api_key`` and scoped.

The ownership rule is absolute: every query under a key filters on
``g.merchant_id``. A key is a merchant's credential, never an account-wide
one, and a row belonging to someone else is reported as missing rather than
forbidden so ids cannot be probed.
"""

import uuid
from datetime import datetime

from flask import g
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.extensions import db
from app.merchant_auth import require_api_key
from app.models.api_key import MerchantApiKey
from app.models.parcel import Parcel
from app.models.shipment import Shipment
from app.models.tracking_event import TrackingEvent
from app.validation import json_object, pagination, shipment_data, text
from app.webhooks.routes import enqueue_event

merchant_ns = Namespace('merchant', description='Merchant API keys and integration')


# ── Key management (dashboard, JWT) ──────────────────────────────────────────

key_input = merchant_ns.model("MerchantApiKeyInput", {
    "name": fields.String(required=True, description="What this key is for"),
    "scopes": fields.List(fields.String, required=True,
                          description=f"Any of: {', '.join(MerchantApiKey.SCOPES)}"),
    "environment": fields.String(enum=[MerchantApiKey.LIVE, MerchantApiKey.TEST]),
    "allowed_ips": fields.List(fields.String, description="Empty means any address"),
    "rate_limit_per_minute": fields.Integer(),
    "expires_at": fields.String(description="ISO 8601; omit for no expiry"),
})


def _validate_scopes(scopes):
    if not isinstance(scopes, list) or not scopes:
        merchant_ns.abort(400, "'scopes' must be a non-empty list")
    for scope in scopes:
        if scope not in MerchantApiKey.SCOPES:
            merchant_ns.abort(400, f"Unknown scope '{scope}'. "
                                   f"Allowed: {', '.join(MerchantApiKey.SCOPES)}")
    return list(dict.fromkeys(scopes))


@merchant_ns.route("/keys")
class ApiKeyList(Resource):
    @merchant_ns.doc("list_api_keys", security="Bearer")
    @merchant_ns.response(200, "Your keys; the secret is never included")
    @jwt_required()
    def get(self):
        """List your API keys. The key itself is never returned here."""
        uid = int(get_jwt_identity())
        keys = (MerchantApiKey.query
                .filter_by(merchant_id=uid)
                .order_by(MerchantApiKey.created_at.desc())
                .all())
        return [k.to_dict() for k in keys]

    @merchant_ns.doc("create_api_key", security="Bearer")
    @merchant_ns.expect(key_input)
    @merchant_ns.response(201, "Created; the plaintext key is returned once")
    @merchant_ns.response(400, "Validation error")
    @jwt_required()
    def post(self):
        """Mint an API key.

        The plaintext is in this response and nowhere else -- only a bcrypt
        hash is stored, so it cannot be recovered afterwards.
        """
        uid = int(get_jwt_identity())
        data = json_object()
        name = text(data.get("name"), "name", 120, required=True)
        scopes = _validate_scopes(data.get("scopes"))

        environment = data.get("environment", MerchantApiKey.LIVE)
        if environment not in (MerchantApiKey.LIVE, MerchantApiKey.TEST):
            merchant_ns.abort(400, "'environment' must be 'live' or 'test'")

        allowed_ips = data.get("allowed_ips", [])
        if not isinstance(allowed_ips, list) or not all(isinstance(i, str) for i in allowed_ips):
            merchant_ns.abort(400, "'allowed_ips' must be a list of strings")

        rate_limit = data.get("rate_limit_per_minute", 120)
        if isinstance(rate_limit, bool) or not isinstance(rate_limit, int) or not 1 <= rate_limit <= 100000:
            merchant_ns.abort(400, "'rate_limit_per_minute' must be an integer between 1 and 100000")

        expires_at = None
        if data.get("expires_at"):
            try:
                expires_at = datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00"))
                expires_at = expires_at.replace(tzinfo=None)
            except ValueError:
                merchant_ns.abort(400, "'expires_at' must be an ISO 8601 timestamp")

        plaintext, prefix, key_hash = MerchantApiKey.generate(environment)
        record = MerchantApiKey(
            merchant_id=uid,
            name=name,
            environment=environment,
            key_prefix=prefix,
            key_hash=key_hash,
            scopes=scopes,
            allowed_ips=allowed_ips,
            rate_limit_per_minute=rate_limit,
            expires_at=expires_at,
            is_active=True,
            created_by=uid,
        )
        db.session.add(record)
        db.session.commit()

        body = record.to_dict()
        body["api_key"] = plaintext
        body["api_key_notice"] = (
            "Copy this key now. It is shown only once and is stored as a hash, "
            "so it cannot be shown again. Revoke and mint a new one if it is lost."
        )
        return body, 201


@merchant_ns.route("/keys/<int:key_id>")
@merchant_ns.response(404, "Key not found")
@merchant_ns.param("key_id", "The key identifier")
class ApiKeyDetail(Resource):
    @merchant_ns.doc("revoke_api_key", security="Bearer")
    @merchant_ns.response(200, "Revoked")
    @jwt_required()
    def delete(self, key_id):
        """Revoke one of your API keys. Takes effect on the next call."""
        uid = int(get_jwt_identity())
        record = db.session.get(MerchantApiKey, key_id)
        if not record or record.merchant_id != uid:
            merchant_ns.abort(404, "Key not found")
        if record.revoked_at:
            return {"msg": "Key was already revoked", "key": record.to_dict()}
        record.is_active = False
        record.revoked_at = datetime.utcnow()
        record.revoked_by = uid
        db.session.commit()
        return {"msg": "Key revoked", "key": record.to_dict()}


# ── The key-authenticated shipping API ───────────────────────────────────────

booking_input = merchant_ns.model("MerchantBookingInput", {
    "origin": fields.String(required=True),
    "destination": fields.String(required=True),
    "reference_no": fields.String(description="Your own order id"),
    "sender_name": fields.String(),
    "sender_address": fields.String(),
    "sender_phone": fields.String(),
    "receiver_name": fields.String(),
    "receiver_address": fields.String(),
    "receiver_phone": fields.String(),
    "total_weight_kg": fields.Float(),
    "total_packages": fields.Integer(),
    "declared_value": fields.Float(),
    "is_cod": fields.Boolean(),
    "cod_amount": fields.Float(),
    "service_id": fields.Integer(),
    "notes": fields.String(),
})

#: Booking fields a merchant may set. Charges, status, rider and branch
#: assignment are priced and decided server-side and are not in this list.
_BOOKING_STRINGS = {
    "origin": 255, "destination": 255, "origin_country": 100, "destination_country": 100,
    "origin_district": 60, "destination_district": 60,
    "sender_name": 200, "sender_address": 2000, "sender_phone": 50, "sender_email": 200,
    "receiver_name": 200, "receiver_address": 2000, "receiver_phone": 50, "receiver_email": 200,
    "contents_description": 2000, "notes": 5000, "currency": 10, "incoterms": 20,
    "reference_no": 100, "service_level": 30, "cod_currency": 10,
}
_BOOKING_FLOATS = ("declared_value", "total_weight_kg", "length_cm", "width_cm",
                   "height_cm", "insurance_amount", "cod_amount")
_BOOKING_BOOLS = ("is_fragile", "is_insured", "is_cod")


def _generate_tracking_no():
    return "MNL" + uuid.uuid4().hex[:9].upper()


def _build_merchant_shipment(data, merchant_id):
    """Create a Shipment from a whitelisted merchant payload."""
    tracking_no = _generate_tracking_no()
    while Shipment.query.filter_by(tracking_no=tracking_no).first():
        tracking_no = _generate_tracking_no()

    shipment = Shipment(
        tracking_no=tracking_no,
        user_id=merchant_id,
        merchant_id=merchant_id,
        status="pending",
        service_id=data.get("service_id"),
    )
    for field, limit in _BOOKING_STRINGS.items():
        if field in data and data[field] is not None:
            setattr(shipment, field, text(data[field], field, limit))
    for field in _BOOKING_FLOATS:
        if field in data and data[field] is not None:
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                merchant_ns.abort(400, f"'{field}' must be a non-negative number")
            setattr(shipment, field, float(value))
    for field in _BOOKING_BOOLS:
        if field in data:
            if not isinstance(data[field], bool):
                merchant_ns.abort(400, f"'{field}' must be true or false")
            setattr(shipment, field, data[field])
    if data.get("total_packages") is not None:
        shipment.total_packages = data["total_packages"]
    if shipment.is_cod:
        shipment.cod_status = "pending"
    return shipment


@merchant_ns.route("/shipments")
class MerchantShipments(Resource):
    @merchant_ns.doc("merchant_list_shipments", security="ApiKey")
    @merchant_ns.response(200, "Your shipments")
    @merchant_ns.response(401, "Invalid or missing API key")
    @merchant_ns.response(403, "Key lacks the shipments:read scope")
    @require_api_key("shipments:read")
    def get(self):
        """List your own shipments (paginated). Requires ``shipments:read``."""
        page, per_page = pagination()
        query = (Shipment.query
                 .filter_by(merchant_id=g.merchant_id)
                 .order_by(Shipment.created_at.desc(), Shipment.id.desc()))
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        return {
            "shipments": [s.to_dict() for s in paginated.items],
            "total": paginated.total,
            "page": paginated.page,
            "pages": paginated.pages,
            "per_page": per_page,
        }

    @merchant_ns.doc("merchant_book_shipment", security="ApiKey")
    @merchant_ns.expect(booking_input)
    @merchant_ns.response(201, "Booked")
    @merchant_ns.response(400, "Validation error")
    @merchant_ns.response(409, "reference_no already used")
    @require_api_key("shipments:write")
    def post(self):
        """Book a shipment. Requires ``shipments:write``."""
        merchant_id = g.merchant_id
        data = shipment_data(json_object())

        reference_no = data.get("reference_no")
        if reference_no:
            # The merchant's own order id is their idempotency handle; a
            # retried POST must not create a second parcel.
            existing = Shipment.query.filter_by(merchant_id=merchant_id,
                                                reference_no=reference_no).first()
            if existing:
                merchant_ns.abort(409, f"reference_no '{reference_no}' is already booked "
                                       f"as {existing.tracking_no}")

        shipment = _build_merchant_shipment(data, merchant_id)
        db.session.add(shipment)
        db.session.flush()

        for parcel in data.get("parcels", []):
            db.session.add(Parcel(
                shipment_id=shipment.id,
                weight_kg=parcel.get("weight_kg", 0),
                description=parcel.get("description", ""),
            ))

        db.session.add(TrackingEvent(
            shipment_id=shipment.id,
            status="pending",
            location=shipment.origin or "",
            description="Shipment booked through the merchant API.",
        ))

        enqueue_event("shipment.created", {
            "event": "shipment.created",
            "tracking_no": shipment.tracking_no,
            "reference_no": shipment.reference_no,
            "status": shipment.status,
            "created_at": datetime.utcnow().isoformat(),
        }, merchant_id=merchant_id)

        db.session.commit()
        return shipment.to_dict(), 201


@merchant_ns.route("/shipments/<string:reference_no>")
@merchant_ns.response(404, "No shipment with that reference")
@merchant_ns.param("reference_no", "Your own order id, as sent at booking")
class MerchantShipmentByReference(Resource):
    @merchant_ns.doc("merchant_get_shipment", security="ApiKey")
    @require_api_key("shipments:read")
    def get(self, reference_no):
        """Fetch one of your shipments by your own reference. ``shipments:read``."""
        shipment = (Shipment.query
                    .filter_by(merchant_id=g.merchant_id, reference_no=reference_no)
                    .order_by(Shipment.created_at.desc())
                    .first())
        if not shipment:
            merchant_ns.abort(404, "No shipment with that reference")
        return shipment.to_dict(include_relations=True)


@merchant_ns.route("/track/<string:tracking_no>")
@merchant_ns.response(404, "No shipment with that tracking number")
@merchant_ns.param("tracking_no", "The Moonlight tracking number")
class MerchantTrack(Resource):
    @merchant_ns.doc("merchant_track", security="ApiKey")
    @require_api_key("tracking:read")
    def get(self, tracking_no):
        """Track one of your shipments. Requires ``tracking:read``.

        Scoped to the key's own merchant -- somebody else's tracking number
        reads as not found, so the endpoint cannot be used to enumerate the
        rest of the network's parcels.
        """
        shipment = Shipment.query.filter_by(tracking_no=tracking_no,
                                            merchant_id=g.merchant_id).first()
        if not shipment:
            merchant_ns.abort(404, "No shipment with that tracking number")
        return {
            "tracking_no": shipment.tracking_no,
            "reference_no": shipment.reference_no,
            "status": shipment.status,
            "origin": shipment.origin,
            "destination": shipment.destination,
            "estimated_delivery": shipment.estimated_delivery.isoformat()
                                  if shipment.estimated_delivery else None,
            "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None,
            "carrier_tracking_no": shipment.carrier_tracking_no,
            "events": [e.to_dict() for e in shipment.events],
        }
