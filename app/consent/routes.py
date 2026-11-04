"""Cookie and processing consent.

The endpoints here are the only way a consent decision enters the system.
Three things about them are deliberate and should survive future edits:

* **The response to "accept all" and "reject all" is identical in shape and
  cost.** A banner that is slower or clumsier to refuse than to accept is
  not freely given consent (recital 42), and that starts at the API.
* **Rejection is recorded, not inferred from silence.** A record saying
  "analytics: no" is evidence; an absent record is only an absence.
* **The decision is read back from the server, never from the browser.**
  The browser copy is a cache. The row is the fact.
"""

from datetime import datetime

from flask import make_response, request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import get_jwt_identity, jwt_required
from werkzeug.exceptions import BadRequest

from app.auth.utils import require_roles
from app.extensions import db, limiter
from app.models.consent import (
    CATEGORY_DESCRIPTIONS,
    CATEGORY_LABELS,
    CONSENT_LIFETIME_DAYS,
    CURRENT_POLICY_VERSION,
    NECESSARY,
    OPTIONAL_CATEGORIES,
    ConsentRecord,
)
from app.net import rate_limit_key
from app.validation import json_object, pagination
from app.visitor import (
    VISITOR_COOKIE,
    consent_state,
    request_fingerprint,
    set_visitor_cookie,
    subject_key,
)

consent_ns = Namespace("consent", description="Cookie and processing consent")


consent_input = consent_ns.model("ConsentInput", {
    "categories": fields.List(
        fields.String(enum=list(OPTIONAL_CATEGORIES)),
        description="Optional categories being granted. Omit or send [] to refuse all.",
    ),
    "source": fields.String(enum=["banner", "preferences", "api"], default="banner"),
})


def _optional_user_id():
    """The signed-in user, when there is one. An anonymous visitor is normal."""
    from flask_jwt_extended import verify_jwt_in_request
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        return int(identity) if identity else None
    except Exception:
        return None


@consent_ns.route("/policy")
class ConsentPolicy(Resource):
    @consent_ns.doc("consent_policy")
    def get(self):
        """The categories on offer, what each one does, and the policy version.

        Public and uncached by the client on purpose: the banner has to show
        the wording that matches the version it will submit against.
        """
        return {
            "policy_version": CURRENT_POLICY_VERSION,
            "lifetime_days": CONSENT_LIFETIME_DAYS,
            "cookie_name": VISITOR_COOKIE,
            "categories": [
                {
                    "key": key,
                    "label": CATEGORY_LABELS[key],
                    "description": CATEGORY_DESCRIPTIONS[key],
                    "required": key == NECESSARY,
                }
                for key in (NECESSARY,) + OPTIONAL_CATEGORIES
            ],
        }, 200


@consent_ns.route("/")
class Consent(Resource):
    @consent_ns.doc("consent_state")
    def get(self):
        """What this visitor has agreed to, as the server sees it."""
        return consent_state(), 200

    @consent_ns.doc("consent_record")
    @consent_ns.expect(consent_input)
    @limiter.limit("30 per minute", key_func=rate_limit_key)
    def post(self):
        """Record a decision. Accepting and refusing cost exactly the same."""
        data = json_object()
        raw = data.get("categories", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list) or any(not isinstance(c, str) for c in raw):
            raise BadRequest("categories must be a list of strings")
        unknown = [c for c in raw if c not in OPTIONAL_CATEGORIES and c != NECESSARY]
        if unknown:
            raise BadRequest(f"Unknown consent categories: {', '.join(sorted(set(unknown))[:4])}")

        source = data.get("source", "banner")
        key = subject_key() or ConsentRecord.new_subject_key()
        evidence = request_fingerprint()

        granted = ConsentRecord.normalise(raw)
        # Refusing everything optional is still a decision, and is recorded as
        # one. 'withdrawn' is reserved for taking back something previously
        # granted, which the DELETE route below handles.
        previous = ConsentRecord.current_for(key)
        action = ConsentRecord.UPDATED if previous else ConsentRecord.GIVEN

        record = ConsentRecord.record(
            key, granted,
            action=action,
            source=source,
            user_id=_optional_user_id(),
            ip=evidence["ip"],
            user_agent=evidence["user_agent"],
            country=evidence["country"],
        )
        db.session.commit()

        response = make_response(record.to_dict(), 201)
        return set_visitor_cookie(response, key)

    @consent_ns.doc("consent_withdraw")
    @limiter.limit("30 per minute", key_func=rate_limit_key)
    def delete(self):
        """Withdraw consent.

        Art. 7(3): withdrawing must be as easy as giving. One call, no
        account needed, and the withdrawal itself is written down — because
        "they withdrew on the 4th" is a thing the company may need to show.
        """
        key = subject_key()
        if not key:
            # Nothing to withdraw, but answer as though there were. The
            # caller learns nothing either way and the banner can treat the
            # two cases identically.
            return consent_state(), 200
        evidence = request_fingerprint()
        ConsentRecord.record(
            key, [],
            action=ConsentRecord.WITHDRAWN,
            source="preferences",
            user_id=_optional_user_id(),
            ip=evidence["ip"],
            user_agent=evidence["user_agent"],
            country=evidence["country"],
        )
        db.session.commit()
        return consent_state(), 200


@consent_ns.route("/me")
class MyConsentHistory(Resource):
    @consent_ns.doc("consent_my_history", security="Bearer")
    @jwt_required()
    def get(self):
        """Every consent decision tied to the signed-in account.

        Part of the access right (Art. 15): a person can see the evidence the
        company holds about their own consent without filing a request.
        """
        uid = int(get_jwt_identity())
        key = subject_key()
        query = ConsentRecord.query.filter(
            db.or_(ConsentRecord.user_id == uid,
                   ConsentRecord.subject_key == key if key else False)
        ).order_by(ConsentRecord.created_at.desc())
        return {"items": [r.to_dict() for r in query.limit(100).all()]}, 200


# ── Administration ───────────────────────────────────────────────────────────

@consent_ns.route("/records")
class ConsentRecords(Resource):
    @consent_ns.doc("consent_records", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """The consent log, paginated.

        Filters: ``subject_key``, ``user_id``, ``action``, ``category``,
        ``policy_version``, ``from``/``to`` (ISO dates).

        This is the table to open when a regulator asks to see that consent
        was obtained. The ``ip_hash`` is included; the address is not stored.
        """
        page, per_page = pagination()
        query = ConsentRecord.query

        if key := request.args.get("subject_key"):
            query = query.filter(ConsentRecord.subject_key == key[:64])
        if uid := request.args.get("user_id"):
            try:
                query = query.filter(ConsentRecord.user_id == int(uid))
            except ValueError:
                raise BadRequest("user_id must be an integer")
        if action := request.args.get("action"):
            query = query.filter(ConsentRecord.action == action[:20])
        if version := request.args.get("policy_version"):
            query = query.filter(ConsentRecord.policy_version == version[:40])
        for arg, column, op in (("from", ConsentRecord.created_at, "ge"),
                                ("to", ConsentRecord.created_at, "le")):
            if value := request.args.get(arg):
                try:
                    moment = datetime.fromisoformat(value)
                except ValueError:
                    raise BadRequest(f"{arg} must be an ISO date")
                query = query.filter(column >= moment if op == "ge" else column <= moment)

        page_obj = (query.order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
                    .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "items": [r.to_dict(include_evidence=True) for r in page_obj.items],
            "total": page_obj.total,
            "page": page,
            "per_page": per_page,
            "pages": page_obj.pages,
        }, 200


@consent_ns.route("/summary")
class ConsentSummary(Resource):
    @consent_ns.doc("consent_summary", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """Acceptance rates per category, over the records still in force.

        Useful as a product signal, and as the number to check after the
        banner wording changes — a rate that jumps suspiciously after a
        redesign usually means the redesign made refusing harder.
        """
        now = datetime.utcnow()
        live = ConsentRecord.query.filter(
            ConsentRecord.policy_version == CURRENT_POLICY_VERSION,
            db.or_(ConsentRecord.expires_at.is_(None), ConsentRecord.expires_at > now),
        )
        # One row per visitor: the most recent decision is the one in force.
        latest = {}
        for record in live.order_by(ConsentRecord.created_at.asc()).all():
            latest[record.subject_key] = record

        decided = list(latest.values())
        total = len(decided)
        counts = {c: 0 for c in OPTIONAL_CATEGORIES}
        withdrawn = 0
        for record in decided:
            if record.action == ConsentRecord.WITHDRAWN:
                withdrawn += 1
                continue
            for category in OPTIONAL_CATEGORIES:
                if record.allows(category):
                    counts[category] += 1

        return {
            "policy_version": CURRENT_POLICY_VERSION,
            "decisions_in_force": total,
            "withdrawn": withdrawn,
            "accepted": {
                category: {
                    "count": counts[category],
                    "rate": round(counts[category] / total * 100, 1) if total else 0.0,
                }
                for category in OPTIONAL_CATEGORIES
            },
            "total_records": ConsentRecord.query.count(),
        }, 200
