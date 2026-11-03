"""Payments and gateway configuration.

Nepal's wallets and bank rails each want slightly different fields, so the
connection details are configuration rather than code. The endpoints here
cover that configuration, the payment records, and the initiate / verify /
callback lifecycle.

**No outbound call to a provider is made yet.** Every provider interaction
funnels through the seam at the bottom of this module (``_PROVIDERS`` and
``call_provider``): one small function per gateway, each of which currently
reports "not enabled". Fill one in and the endpoints start working for that
gateway with no other change. Nothing here fakes a successful payment.

Endpoints:
  GET    /api/payments/gateways          Public: active gateways, no secrets
  GET    /api/payments/gateways/all      super_admin: all gateways, masked secrets
  POST   /api/payments/gateways          super_admin: create
  GET    /api/payments/gateways/<id>     super_admin: detail, masked secrets
  PUT    /api/payments/gateways/<id>     super_admin: update
  DELETE /api/payments/gateways/<id>     super_admin: soft deactivate
  GET    /api/payments                   List (admin: all, customer: own)
  POST   /api/payments/initiate          Create a payment; honours idempotency_key
  GET    /api/payments/<id>              Owner or admin
  POST   /api/payments/<id>/verify       Provider seam
  POST   /api/payments/callback/<code>   Provider seam (webhook landing pad)
  POST   /api/payments/<id>/refund       super_admin
"""
from datetime import datetime

from flask import request, current_app
from flask_restx import Namespace, Resource
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.exceptions import BadRequest

from app.extensions import db
from app.auth.utils import require_roles
from app.validation import json_object, number, pagination, text
from app.models.payment import Payment, PaymentGateway
from app.models.shipment import Shipment
from app.models.invoice import Invoice
from app.models.user import User

payment_ns = Namespace('payments', description='Payments and gateway configuration')

#: Gateway columns an operator may write. Never mass-assign: the ciphertext
#: columns and the timestamps are deliberately absent.
GATEWAY_WRITABLE = (
    "name", "display_name", "logo_url", "instructions",
    "is_active", "is_sandbox", "sort_order",
    "api_base_url", "sandbox_base_url", "initiate_path", "verify_path",
    "refund_path", "status_path",
    "success_url", "failure_url", "callback_url",
    "merchant_code", "client_id", "signature_algo", "signature_header",
    "supported_currencies", "min_amount", "max_amount",
    "service_charge_percent", "service_charge_flat",
    "timeout_seconds", "max_retries",
)

#: Plaintext credentials, written through the EncryptedField descriptors so
#: only ciphertext reaches the database. Never echoed back in a response.
GATEWAY_SECRETS = ("public_key", "secret_key", "client_secret")

_BOOL_FIELDS = ("is_active", "is_sandbox")
_INT_FIELDS = ("sort_order", "timeout_seconds", "max_retries")
_FLOAT_FIELDS = ("min_amount", "max_amount", "service_charge_percent", "service_charge_flat")
_TEXT_LIMITS = {
    "name": 120, "display_name": 120, "logo_url": 500, "instructions": 5000,
    "api_base_url": 500, "sandbox_base_url": 500, "initiate_path": 300,
    "verify_path": 300, "refund_path": 300, "status_path": 300,
    "success_url": 500, "failure_url": 500, "callback_url": 500,
    "merchant_code": 120, "client_id": 255, "signature_algo": 30,
    "signature_header": 80,
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _current_user():
    user = db.session.get(User, int(get_jwt_identity()))
    if not user:
        payment_ns.abort(404, "User not found")
    return user


def _is_admin(user):
    return any(r.name in ("super_admin", "admin") for r in user.roles)


def _gateway_or_404(id):
    gateway = db.session.get(PaymentGateway, id)
    if not gateway:
        payment_ns.abort(404, "Gateway not found")
    return gateway


def _apply_gateway_fields(gateway, data):
    """Copy whitelisted fields onto a gateway, coercing and bounding each.

    A security audit flagged blind ``setattr`` here, so every field is named
    and typed. Unknown keys are dropped silently rather than written.
    """
    for key in GATEWAY_WRITABLE:
        if key not in data:
            continue
        value = data[key]
        if key in _BOOL_FIELDS:
            if not isinstance(value, bool):
                raise BadRequest(f"{key} must be true or false")
        elif key in _INT_FIELDS:
            if isinstance(value, bool) or type(value) is not int or value < 0:
                raise BadRequest(f"{key} must be a non-negative integer")
        elif key in _FLOAT_FIELDS:
            if value is None:
                pass
            else:
                value = round(float(number(value, key)), 2)
        elif key == "supported_currencies":
            if not isinstance(value, list) or not value or len(value) > 20 or \
                    not all(isinstance(c, str) and 0 < len(c) <= 10 for c in value):
                raise BadRequest("supported_currencies must be a list of 1-20 currency codes")
            value = [c.strip().upper() for c in value]
        elif value is None:
            pass
        else:
            value = text(value, key, _TEXT_LIMITS[key])
        setattr(gateway, key, value)

    for key in GATEWAY_SECRETS:
        if key in data and data[key]:
            # Assigning the descriptor encrypts; the column holds ciphertext.
            setattr(gateway, key, text(data[key], key, 2000, required=True))


def _service_charge(gateway, amount):
    return round(amount * (gateway.service_charge_percent or 0.0) / 100.0
                 + (gateway.service_charge_flat or 0.0), 2)


def _mark_paid(payment):
    """Settle a payment and push the result onto the linked shipment.

    Only the provider seam should reach this. It is the single place that
    writes ``success``, so the shipment's payment status can never drift from
    the payment record.
    """
    payment.status = Payment.SUCCESS
    payment.paid_at = payment.paid_at or datetime.utcnow()
    if payment.shipment:
        payment.shipment.payment_status = "paid"
    return payment


# ── gateway configuration ────────────────────────────────────────────────────

@payment_ns.route("/gateways")
class GatewayList(Resource):
    @payment_ns.doc("list_active_gateways")
    @payment_ns.response(200, "Active gateways, no credentials")
    def get(self):
        """Active gateways for the checkout page. Public, and never includes credentials."""
        gateways = (PaymentGateway.query
                    .filter_by(is_active=True)
                    .order_by(PaymentGateway.sort_order.asc(), PaymentGateway.id.asc())
                    .all())
        return [g.to_dict() for g in gateways]

    @payment_ns.doc("create_gateway", security="Bearer")
    @payment_ns.response(201, "Gateway created")
    @payment_ns.response(400, "Unknown gateway code")
    @payment_ns.response(409, "Gateway code already configured")
    @require_roles("super_admin")
    def post(self):
        """Configure a new gateway. ``code`` must be one of PaymentGateway.KNOWN_CODES."""
        data = json_object()
        code = text(data.get("code", ""), "code", 40, required=True).lower()
        if code not in PaymentGateway.KNOWN_CODES:
            raise BadRequest(f"code must be one of: {', '.join(PaymentGateway.KNOWN_CODES)}")
        if PaymentGateway.query.filter_by(code=code).first():
            payment_ns.abort(409, "That gateway code is already configured")

        gateway = PaymentGateway(code=code, name=text(data.get("name", "") or code, "name", 120,
                                                      required=True))
        _apply_gateway_fields(gateway, data)
        db.session.add(gateway)
        db.session.commit()
        return gateway.to_dict(include_secrets=True), 201


@payment_ns.route("/gateways/all")
class GatewayListAll(Resource):
    @payment_ns.doc("list_all_gateways", security="Bearer")
    @payment_ns.response(200, "Every gateway, credentials masked")
    @require_roles("super_admin")
    def get(self):
        """Every gateway including inactive ones. Credentials are masked, never plaintext."""
        gateways = (PaymentGateway.query
                    .order_by(PaymentGateway.sort_order.asc(), PaymentGateway.id.asc())
                    .all())
        return [g.to_dict(include_secrets=True) for g in gateways]


@payment_ns.route("/gateways/<int:id>")
@payment_ns.param("id", "The gateway identifier")
class GatewayDetail(Resource):
    @payment_ns.doc("get_gateway", security="Bearer")
    @payment_ns.response(200, "Gateway, credentials masked")
    @require_roles("super_admin")
    def get(self, id):
        """Gateway configuration with masked credentials."""
        return _gateway_or_404(id).to_dict(include_secrets=True)

    @payment_ns.doc("update_gateway", security="Bearer")
    @payment_ns.response(200, "Gateway updated")
    @require_roles("super_admin")
    def put(self, id):
        """Update a gateway. ``code`` is immutable; credentials are re-encrypted on write."""
        gateway = _gateway_or_404(id)
        data = json_object()
        if data.get("code") and data["code"].lower() != gateway.code:
            raise BadRequest("A gateway code cannot be changed; create a new gateway instead")
        _apply_gateway_fields(gateway, data)
        db.session.commit()
        return gateway.to_dict(include_secrets=True)

    @payment_ns.doc("deactivate_gateway", security="Bearer")
    @payment_ns.response(200, "Gateway deactivated")
    @require_roles("super_admin")
    def delete(self, id):
        """Soft-deactivate a gateway. Its payment history is kept."""
        gateway = _gateway_or_404(id)
        gateway.is_active = False
        db.session.commit()
        return {"msg": "Gateway deactivated", "id": gateway.id, "is_active": gateway.is_active}


# ── payments ─────────────────────────────────────────────────────────────────

@payment_ns.route("")
@payment_ns.route("/")
class PaymentList(Resource):
    @payment_ns.doc("list_payments", security="Bearer",
                    params={"status": "Filter by payment status",
                            "gateway_id": "Filter by gateway",
                            "shipment_id": "Filter by shipment"})
    @payment_ns.response(200, "Paginated payments")
    @jwt_required()
    def get(self):
        """List payments. Admins see all; a customer sees only their own."""
        user = _current_user()
        page, per_page = pagination()

        query = Payment.query
        if not _is_admin(user):
            query = query.filter(Payment.user_id == user.id)
        elif request.args.get("user_id", type=int):
            query = query.filter(Payment.user_id == request.args.get("user_id", type=int))

        status = request.args.get("status")
        if status:
            if status not in Payment.STATUSES:
                raise BadRequest(f"status must be one of: {', '.join(Payment.STATUSES)}")
            query = query.filter(Payment.status == status)
        if request.args.get("gateway_id", type=int):
            query = query.filter(Payment.gateway_id == request.args.get("gateway_id", type=int))
        if request.args.get("shipment_id", type=int):
            query = query.filter(Payment.shipment_id == request.args.get("shipment_id", type=int))

        pag = query.order_by(Payment.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        return {
            "items": [p.to_dict() for p in pag.items],
            "total": pag.total,
            "page": pag.page,
            "pages": pag.pages,
            "per_page": per_page,
        }


@payment_ns.route("/initiate")
class PaymentInitiate(Resource):
    @payment_ns.doc("initiate_payment", security="Bearer")
    @payment_ns.response(200, "Existing payment returned for a repeated idempotency_key")
    @payment_ns.response(201, "Payment initiated")
    @payment_ns.response(400, "Gateway inactive or unconfigured, or the amount is wrong")
    @jwt_required()
    def post(self):
        """Open a payment against a shipment or invoice.

        The amount is checked against the linked document's server-computed
        total and against the gateway's limits. A repeated ``idempotency_key``
        returns the original payment instead of charging twice.
        """
        user = _current_user()
        data = json_object()

        idempotency_key = text(data.get("idempotency_key", ""), "idempotency_key", 80)
        if idempotency_key:
            existing = Payment.query.filter_by(idempotency_key=idempotency_key).first()
            if existing:
                if existing.user_id != user.id and not _is_admin(user):
                    payment_ns.abort(409, "That idempotency key belongs to another payment")
                return existing.to_dict(), 200

        gateway_id = data.get("gateway_id")
        gateway_code = data.get("gateway_code")
        if isinstance(gateway_id, int) and not isinstance(gateway_id, bool):
            gateway = db.session.get(PaymentGateway, gateway_id)
        elif isinstance(gateway_code, str):
            gateway = PaymentGateway.query.filter_by(code=gateway_code.strip().lower()).first()
        else:
            raise BadRequest("gateway_id or gateway_code is required")
        if not gateway:
            raise BadRequest("Unknown payment gateway")
        if not gateway.is_active:
            payment_ns.abort(400, f"The {gateway.code} gateway is not active")
        if not gateway.is_configured:
            payment_ns.abort(400, f"The {gateway.code} gateway is not configured yet")

        shipment_id = data.get("shipment_id")
        invoice_id = data.get("invoice_id")
        shipment = invoice = None
        if isinstance(shipment_id, int) and not isinstance(shipment_id, bool):
            shipment = db.session.get(Shipment, shipment_id)
            if not shipment:
                raise BadRequest("shipment_id does not exist")
            if not _is_admin(user) and user.id not in (shipment.user_id, shipment.merchant_id):
                payment_ns.abort(403, "You cannot pay for another customer's shipment")
            expected = round(float(shipment.total_charge or 0.0), 2)
            expected_label = "shipment total charge"
        elif isinstance(invoice_id, int) and not isinstance(invoice_id, bool):
            invoice = db.session.get(Invoice, invoice_id)
            if not invoice:
                raise BadRequest("invoice_id does not exist")
            if not _is_admin(user) and invoice.created_by != user.id:
                payment_ns.abort(403, "You cannot pay for another customer's invoice")
            expected = round(float(invoice.total_amount or 0.0), 2)
            expected_label = "invoice total"
        else:
            raise BadRequest("shipment_id or invoice_id is required")

        if expected <= 0:
            payment_ns.abort(400, f"The {expected_label} is zero; there is nothing to pay")

        # The client may state the amount, but only to agree with the server.
        if data.get("amount") is not None:
            stated = round(float(number(data["amount"], "amount")), 2)
            if abs(stated - expected) >= 0.01:
                payment_ns.abort(400, f"amount must equal the {expected_label} of {expected}")
        amount = expected

        currency = text(data.get("currency", "") or "NPR", "currency", 10).upper()
        supported = [c.upper() for c in (gateway.supported_currencies or ["NPR"])]
        if currency not in supported:
            payment_ns.abort(400, f"The {gateway.code} gateway does not accept {currency}")
        if gateway.min_amount is not None and amount < gateway.min_amount:
            payment_ns.abort(400, f"Minimum amount for {gateway.code} is {gateway.min_amount}")
        if gateway.max_amount is not None and amount > gateway.max_amount:
            payment_ns.abort(400, f"Maximum amount for {gateway.code} is {gateway.max_amount}")

        payment = Payment(
            payment_no=Payment.generate_number(),
            user_id=user.id,
            shipment_id=shipment.id if shipment else None,
            invoice_id=invoice.id if invoice else None,
            gateway_id=gateway.id,
            amount=amount,
            currency=currency,
            service_charge=_service_charge(gateway, amount),
            status=Payment.INITIATED,
            idempotency_key=idempotency_key or None,
        )
        db.session.add(payment)
        db.session.commit()

        seam = call_provider("initiate", payment)
        body = payment.to_dict()
        body["gateway"] = {
            "code": gateway.code,
            "display_name": gateway.display_name or gateway.name,
            "is_sandbox": gateway.is_sandbox,
        }
        body["provider"] = seam
        return body, 201


@payment_ns.route("/<int:id>")
@payment_ns.param("id", "The payment identifier")
class PaymentDetail(Resource):
    @payment_ns.doc("get_payment", security="Bearer")
    @payment_ns.response(200, "Payment")
    @payment_ns.response(403, "Access denied")
    @jwt_required()
    def get(self, id):
        """Payment detail. The payer, or an admin."""
        user = _current_user()
        payment = db.session.get(Payment, id)
        if not payment:
            payment_ns.abort(404, "Payment not found")
        if payment.user_id != user.id and not _is_admin(user):
            payment_ns.abort(403, "Access denied")
        return payment.to_dict()


@payment_ns.route("/<int:id>/verify")
@payment_ns.param("id", "The payment identifier")
class PaymentVerify(Resource):
    @payment_ns.doc("verify_payment", security="Bearer")
    @payment_ns.response(200, "Verification result from the provider seam")
    @payment_ns.response(501, "Gateway integration not enabled")
    @jwt_required()
    def post(self, id):
        """Ask the provider whether the payment went through.

        Until a connector is filled in at the seam below, this leaves the
        payment exactly as it was and says so. It never invents a success.
        """
        user = _current_user()
        payment = db.session.get(Payment, id)
        if not payment:
            payment_ns.abort(404, "Payment not found")
        if payment.user_id != user.id and not _is_admin(user):
            payment_ns.abort(403, "Access denied")

        result = call_provider("verify", payment, request.get_json(silent=True) or {})
        changed = apply_provider_result(payment, result)
        if changed:
            db.session.commit()
        return {"payment": payment.to_dict(), "provider": result}, (200 if result["enabled"] else 501)


@payment_ns.route("/callback/<string:gateway_code>")
@payment_ns.param("gateway_code", "The gateway that is calling back")
class PaymentCallback(Resource):
    @payment_ns.doc("payment_callback")
    @payment_ns.response(200, "Callback recorded")
    @payment_ns.response(501, "Gateway integration not enabled")
    def post(self, gateway_code):
        """Landing pad for a provider's server-to-server callback.

        The raw payload is stored against the payment for later replay, but
        the status only moves once the seam for this gateway is implemented
        and can verify the callback's signature. An unverified callback must
        never be enough to mark money received.
        """
        payload = request.get_json(silent=True) or request.form.to_dict() or {}
        gateway = PaymentGateway.query.filter_by(code=(gateway_code or "").lower()).first()
        if not gateway:
            payment_ns.abort(404, "Unknown gateway")

        reference = str(payload.get("payment_no") or payload.get("transaction_id")
                        or payload.get("pid") or "").strip()
        payment = None
        if reference:
            payment = (Payment.query.filter_by(payment_no=reference).first()
                       or Payment.query.filter_by(transaction_id=reference).first())
        if payment is None:
            current_app.logger.warning(
                "Unmatched %s callback; no payment reference in payload", gateway.code)
            payment_ns.abort(404, "No payment matches that reference")

        # Keep the evidence; do not act on it yet. The body is unauthenticated,
        # so only a bounded slice of it is stored.
        if not isinstance(payload, dict) or len(payload) > 50:
            raise BadRequest("Callback payload must be an object of at most 50 fields")
        payment.gateway_response = {k: v for k, v in payload.items()
                                    if not isinstance(v, str) or len(v) <= 2000}
        result = call_provider("callback", payment, payload)
        apply_provider_result(payment, result)
        db.session.commit()
        return {"payment": payment.to_dict(), "provider": result}, (200 if result["enabled"] else 501)


@payment_ns.route("/<int:id>/refund")
@payment_ns.param("id", "The payment identifier")
class PaymentRefund(Resource):
    @payment_ns.doc("refund_payment", security="Bearer")
    @payment_ns.response(200, "Refund recorded")
    @payment_ns.response(400, "Refund exceeds the paid amount, or payment is not refundable")
    @payment_ns.response(403, "Only super_admin may refund")
    @require_roles("super_admin")
    def post(self, id):
        """Record a refund against a successful payment.

        The refund is bounded by what was actually paid, less anything already
        refunded. Moving the money itself is the provider's job and lives at
        the seam below.
        """
        payment = db.session.get(Payment, id)
        if not payment:
            payment_ns.abort(404, "Payment not found")
        if payment.status not in (Payment.SUCCESS, Payment.REFUNDED):
            payment_ns.abort(400, f"Only a successful payment can be refunded; this one is {payment.status}")

        data = json_object()
        already = round(float(payment.refund_amount or 0.0), 2)
        refundable = round(float(payment.amount or 0.0) - already, 2)
        if refundable <= 0:
            payment_ns.abort(400, "This payment has already been fully refunded")

        requested = data.get("amount")
        amount = refundable if requested is None else round(float(number(requested, "amount")), 2)
        if amount <= 0:
            payment_ns.abort(400, "Refund amount must be positive")
        if amount - refundable >= 0.01:
            payment_ns.abort(400, f"Refund of {amount} exceeds the refundable amount of {refundable}")

        reason = text(data.get("reason", ""), "reason", 255)
        result = call_provider("refund", payment, {"amount": amount})

        payment.refund_amount = round(already + amount, 2)
        payment.refunded_at = datetime.utcnow()
        if reason:
            payment.refund_reason = reason
        if payment.refund_amount + 0.01 >= round(float(payment.amount or 0.0), 2):
            payment.status = Payment.REFUNDED
            if payment.shipment:
                payment.shipment.payment_status = "refunded"
        db.session.commit()
        return {"payment": payment.to_dict(), "provider": result}


# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER SEAM
#
# This is the only place that is allowed to talk to a payment provider, and
# today it talks to none of them. Each function below owns one gateway. To
# switch a provider on:
#
#   1. Implement its function. Read endpoints and credentials off `gateway`
#      (`gateway.base_url`, `gateway.verify_path`, `gateway.secret_key`, ...);
#      none of that belongs in code.
#   2. Return a dict shaped like `_not_enabled` below, with `enabled: True`
#      and `status` set to one of `Payment.STATUSES`.
#   3. `apply_provider_result` takes it from there, including updating the
#      linked shipment when a payment reaches `success`.
#
# Until then every call returns `enabled: False` and the payment is left
# untouched. Do not shortcut this by writing `success` anywhere else.
# ─────────────────────────────────────────────────────────────────────────────

def _not_enabled(gateway_code, operation):
    return {
        "enabled": False,
        "status": None,
        "gateway_code": gateway_code,
        "operation": operation,
        "message": (f"Gateway integration not enabled: the {gateway_code} connector "
                    f"has no {operation} implementation yet. The payment was left unchanged."),
    }


def _esewa(operation, payment, payload):
    """eSewa ePay v2. See gateway.api_base_url / verify_path for the endpoints."""
    return _not_enabled("esewa", operation)


def _khalti(operation, payment, payload):
    """Khalti ePayment. Server-side lookup with the secret key as an auth header."""
    return _not_enabled("khalti", operation)


def _fonepay(operation, payment, payload):
    """Fonepay merchant API. HMAC-signed query against gateway.signature_algo."""
    return _not_enabled("fonepay", operation)


def _connectips(operation, payment, payload):
    """ConnectIPS. Requires the merchant's signed token and creditor account."""
    return _not_enabled("connectips", operation)


def _imepay(operation, payment, payload):
    """IME Pay. Token-based confirm/verify pair."""
    return _not_enabled("imepay", operation)


def _manual(operation, payment, payload):
    """Cash, cheque and bank transfer: settled by an operator, not an API."""
    return _not_enabled(payment.gateway.code if payment.gateway else "manual", operation)


#: gateway code -> connector. A code absent from this map is simply not wired.
_PROVIDERS = {
    "esewa": _esewa,
    "khalti": _khalti,
    "fonepay": _fonepay,
    "connectips": _connectips,
    "imepay": _imepay,
    "cash": _manual,
    "cheque": _manual,
    "bank_transfer": _manual,
}


def call_provider(operation, payment, payload=None):
    """Route one operation to the connector for this payment's gateway."""
    code = payment.gateway.code if payment.gateway else None
    connector = _PROVIDERS.get(code)
    if connector is None:
        return _not_enabled(code or "unknown", operation)
    return connector(operation, payment, payload or {})


def apply_provider_result(payment, result):
    """Apply a connector's verdict to the payment. Returns True if anything moved.

    A result from a connector that is not enabled changes nothing, which is
    the whole point: no provider, no state transition.
    """
    if not result.get("enabled"):
        return False
    status = result.get("status")
    if status not in Payment.STATUSES or status == payment.status:
        return False

    payment.transaction_id = result.get("transaction_id") or payment.transaction_id
    payment.gateway_reference = result.get("gateway_reference") or payment.gateway_reference
    if isinstance(result.get("raw"), dict):
        payment.gateway_response = result["raw"]

    if status == Payment.SUCCESS:
        _mark_paid(payment)
    else:
        payment.status = status
        if status == Payment.FAILED:
            payment.failure_reason = (result.get("message") or "")[:255] or None
    return True
