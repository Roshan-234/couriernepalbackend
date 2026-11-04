"""Serving promotions, and the admin side that manages them.

The public half of this namespace is four endpoints that a visitor's browser
touches on an ordinary page load, so they are small, cheap and say as little
as possible. The admin half is ordinary CRUD with a state machine bolted to
the front of it, because a campaign's status decides whether something is on
the live site and must not be settable by a stray PUT.

Three rules are enforced here rather than left to an editor's discretion:

* **A site-relative path, or nothing.** ``target_path``, ``landing_path`` and
  ``link_path`` are admin-editable fields that end up inside an ``href``. A
  field that accepts ``javascript:`` is stored XSS and a field that accepts
  ``https://somewhere.else`` is an open redirect wearing the company's name.
  Both are rejected at the boundary, for every such field, on every write.
* **An image that carries the offer needs alt text.** The model says so; a
  comment does not enforce anything, so activation refuses without it.
* **Status moves through the lifecycle endpoints.** ``activate``, ``pause``
  and ``finish`` know which transitions make sense. ``PUT`` does not get to
  guess.

Image handling: ``image_path`` is accepted as a string and stored as one.
**There is no upload endpoint in this codebase** — nothing under ``app/``
writes an uploaded file anywhere — so the path or URL must point at
something the site already serves or at the CDN. Wiring uploads is a
separate piece of work and is deliberately not half-started here.
"""

import inspect
from datetime import datetime

from flask import current_app, make_response, request
from flask_restx import Namespace, Resource, fields
from werkzeug.exceptions import BadRequest, Conflict, NotFound, TooManyRequests

from app.advertising import throttle
from app.auth.utils import require_roles
from app.extensions import db, limiter
from app.models.advertising import AdCampaign, AdCreative, AdPlacement, SiteNotice
from app.models.consent import PREFERENCES, hash_identifier
from app.net import client_ip, rate_limit_key
from app.validation import json_object, pagination, text
from app.visitor import allows, country_hint, subject_key

ads_ns = Namespace("ads", description="Promotions, campaigns and site notices")

#: The analytics collector is being built in parallel and may not exist yet.
#: Advert counting must not depend on it: the counters on the row are the
#: module's own record, and the event log is a richer duplicate for whoever
#: has it. Import defensively and degrade to counter-only.
try:  # pragma: no cover - depends on whether the analytics module has landed
    from app.analytics.collect import record_event
except ImportError:  # pragma: no cover
    record_event = None


# ── Validation ───────────────────────────────────────────────────────────────

#: Characters that have no business in a site-relative path and that are the
#: usual way out of one. The backslash matters on its own: some browsers
#: normalise ``/\evil.com`` into a protocol-relative URL.
_FORBIDDEN_PATH_CHARS = set("\\<>\"'`{}|^") | {chr(c) for c in range(0x21)} | {"\x7f"}


def site_path(value, field, *, required=False, limit=300):
    """A path on this site, or nothing at all.

    Accepts ``/pricing``, ``/blog/dashain-cut-off``, ``/blog*``. Rejects
    ``javascript:...``, ``https://elsewhere``, ``//elsewhere`` and
    ``/\\elsewhere`` — anything that is an absolute or protocol-relative
    reference wearing a leading slash.

    The check is a whitelist in spirit: it must start with a single ``/``
    and contain nothing from :data:`_FORBIDDEN_PATH_CHARS`. Relative paths
    are refused too, because a bare ``pricing`` resolves against whatever
    page it happens to be rendered on.
    """
    if value is None or value == "":
        if required:
            raise BadRequest(f"'{field}' is required")
        return None
    if not isinstance(value, str):
        raise BadRequest(f"'{field}' must be a site-relative path such as /pricing")
    candidate = value.strip()
    if len(candidate) > limit:
        raise BadRequest(f"'{field}' must be at most {limit} characters")
    if not candidate.startswith("/"):
        raise BadRequest(
            f"'{field}' must be a site-relative path starting with '/'. "
            "Absolute URLs and scheme-prefixed values are not accepted."
        )
    if candidate.startswith("//") or candidate.startswith("/\\"):
        raise BadRequest(f"'{field}' must not be a protocol-relative URL")
    if any(ch in _FORBIDDEN_PATH_CHARS for ch in candidate):
        raise BadRequest(f"'{field}' contains characters that are not valid in a path")
    return candidate


def _path_list(value, field, limit=50):
    """A list of page paths a creative or notice may appear on."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise BadRequest(f"'{field}' must be a list of site-relative paths")
    if len(value) > limit:
        raise BadRequest(f"'{field}' may contain at most {limit} paths")
    return [site_path(item, field, required=True) for item in value]


def _country_list(value, field="countries", limit=60):
    """Two-letter country codes, upper-cased. Geography is the only audience."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise BadRequest(f"'{field}' must be a list of two-letter country codes")
    if len(value) > limit:
        raise BadRequest(f"'{field}' may contain at most {limit} codes")
    codes = []
    for item in value:
        if not isinstance(item, str) or len(item.strip()) != 2 or not item.strip().isalpha():
            raise BadRequest(f"'{field}' entries must be two-letter country codes, e.g. NP")
        codes.append(item.strip().upper())
    return codes


def _moment(value, field):
    """An ISO timestamp, or None to clear it."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise BadRequest(f"'{field}' must be an ISO 8601 timestamp or null")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise BadRequest(f"'{field}' must be an ISO 8601 timestamp or null")


def _integer(value, field, minimum=None, maximum=None):
    if not isinstance(value, int) or isinstance(value, bool):
        raise BadRequest(f"'{field}' must be an integer")
    if minimum is not None and value < minimum:
        raise BadRequest(f"'{field}' must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise BadRequest(f"'{field}' must be at most {maximum}")
    return value


def _boolean(value, field):
    if not isinstance(value, bool):
        raise BadRequest(f"'{field}' must be true or false")
    return value


def _reject_unknown(data, allowed):
    """Name the keys that were ignored instead of dropping them in silence.

    An editor who misspells ``image_alt`` and gets a 200 believes the alt
    text was saved.
    """
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise BadRequest(f"Unknown field(s): {', '.join(unknown[:6])}")


def _unique_code(model, code, field="code", current=None):
    existing = model.query.filter_by(code=code).first()
    if existing and (current is None or existing.id != current.id):
        raise Conflict(f"{field} '{code}' is already in use")
    return code


def _language():
    """Which language the visitor is reading in. Two, and English wins ties."""
    requested = (request.args.get("language") or "en").strip().lower()
    return "ne" if requested.startswith("ne") else "en"


def _no_content():
    """204 with a genuinely empty body."""
    return make_response("", 204)


def _visitor_bucket():
    """What an advert event is counted against.

    The consent cookie's subject key when the visitor has one, otherwise a
    salted hash of the address. A visitor who has made no consent decision
    has no cookie by design (see ``app/visitor.py``), so the address hash is
    the only handle available — and it is a hash precisely so the counter
    defence does not become a place raw addresses accumulate.
    """
    return subject_key() or f"ip:{hash_identifier(client_ip())}"


def _record_analytics(name, **fields):
    """Best-effort hand-off to the analytics event log.

    The collector is a sibling module with its own release cycle, and this
    one works without it: the counters on the row are the advertising
    module's own record, and the event log is a richer duplicate for
    whoever has it. So keywords the collector does not accept are dropped
    rather than raising, and any failure at all is swallowed. A public
    counter endpoint must not return 500 because an optional neighbour
    changed shape or was deployed without its table.
    """
    if record_event is None:
        return
    try:
        parameters = inspect.signature(record_event).parameters
        takes_anything = any(p.kind is p.VAR_KEYWORD for p in parameters.values())
        kwargs = fields if takes_anything else {k: v for k, v in fields.items()
                                                if k in parameters}
        record_event(name, **kwargs)
    except Exception:  # noqa: BLE001 - deliberate; see the docstring
        current_app.logger.debug("Advert event %s not recorded to analytics", name,
                                 exc_info=True)


# ── Swagger input models ─────────────────────────────────────────────────────

event_input = ads_ns.model("AdEventInput", {
    "creative_id": fields.Integer(required=True, description="The creative that was shown or clicked"),
    "path": fields.String(description="Site-relative path it was shown on"),
})

campaign_input = ads_ns.model("AdCampaignInput", {
    "code": fields.String(required=True, description="Stable identifier, e.g. DASHAIN_2026"),
    "name": fields.String(required=True),
    "objective": fields.String(),
    "occasion": fields.String(description="Festival or season, e.g. dashain"),
    "landing_path": fields.String(description="Site-relative path, e.g. /pricing"),
    "utm_campaign": fields.String(),
    "starts_at": fields.String(description="ISO 8601, or null"),
    "ends_at": fields.String(description="ISO 8601, or null"),
})

placement_input = ads_ns.model("AdPlacementInput", {
    "code": fields.String(required=True),
    "name": fields.String(required=True),
    "description": fields.String(),
    "recommended_width": fields.Integer(),
    "recommended_height": fields.Integer(),
    "max_creatives": fields.Integer(default=1),
    "is_active": fields.Boolean(default=True),
})

creative_input = ads_ns.model("AdCreativeInput", {
    "placement_id": fields.Integer(required=True),
    "headline": fields.String(required=True),
    "body": fields.String(),
    "call_to_action": fields.String(),
    "format": fields.String(enum=list(AdCreative.FORMATS)),
    "target_path": fields.String(description="Site-relative path only"),
    "image_path": fields.String(description="Path or URL of an existing image; no upload endpoint exists"),
    "image_alt": fields.String(description="Required before activation when image_path is set"),
    "headline_ne": fields.String(),
    "body_ne": fields.String(),
    "page_paths": fields.List(fields.String, description="Empty means anywhere the placement exists"),
    "countries": fields.List(fields.String, description="Two-letter codes; empty means everywhere"),
    "priority": fields.Integer(default=0),
    "is_active": fields.Boolean(default=True),
})

notice_input = ads_ns.model("SiteNoticeInput", {
    "code": fields.String(required=True),
    "title": fields.String(required=True),
    "body": fields.String(),
    "title_ne": fields.String(),
    "body_ne": fields.String(),
    "style": fields.String(enum=list(SiteNotice.STYLES)),
    "tone": fields.String(enum=list(SiteNotice.TONES)),
    "link_path": fields.String(description="Site-relative path only"),
    "link_label": fields.String(),
    "starts_at": fields.String(),
    "ends_at": fields.String(),
    "page_paths": fields.List(fields.String),
    "dismiss_days": fields.Integer(),
    "max_impressions": fields.Integer(),
    "is_dismissible": fields.Boolean(default=True),
    "is_active": fields.Boolean(default=True),
    "priority": fields.Integer(default=0),
})


# ── Public serving ───────────────────────────────────────────────────────────

@ads_ns.route("/serve")
class AdServe(Resource):
    @ads_ns.doc("serve_ads")
    @ads_ns.param("path", "The page being rendered, e.g. /pricing")
    @ads_ns.param("placement", "Restrict to one placement code")
    @ads_ns.param("language", "en or ne")
    def get(self):
        """What should render on this page, decided here rather than in the browser.

        Filtering is: the campaign is live, the creative and its placement
        are active, the creative matches the path and the country, highest
        priority first, and no more than the placement's ``max_creatives``.

        **Country comes from ``country_hint()``, never from the caller.** A
        ``country=`` parameter would let anybody enumerate what every region
        is being shown, which is both a competitive leak and an invitation
        to probe. If the CDN did not say, country is unknown and
        ``AdCreative.matches`` does not filter on it at all — geography is
        a choice of offer, not a gate, so an absent header shows everything
        rather than making every targeted advert disappear on a deployment
        that has no CDN in front of it.

        The response carries the creatives' own counters, because the
        model's ``to_dict`` is the contract the frontend reads. They are
        aggregate numbers about the company's own adverts, not about the
        visitor.
        """
        path = site_path(request.args.get("path") or "/", "path", required=True)
        language = _language()
        country = country_hint()
        placement_code = (request.args.get("placement") or "").strip()[:60]

        now = datetime.utcnow()
        query = (AdCreative.query
                 .join(AdCampaign, AdCreative.campaign_id == AdCampaign.id)
                 .join(AdPlacement, AdCreative.placement_id == AdPlacement.id)
                 .filter(AdCreative.is_active.is_(True),
                         AdPlacement.is_active.is_(True),
                         AdCampaign.status == AdCampaign.RUNNING))
        if placement_code:
            query = query.filter(AdPlacement.code == placement_code)

        candidates = query.order_by(AdCreative.priority.desc(), AdCreative.id.asc()).all()

        shown = []
        per_placement = {}
        for creative in candidates:
            if not creative.campaign or not creative.campaign.is_live(now):
                continue
            if not creative.matches(path, country):
                continue
            cap = (creative.placement.max_creatives if creative.placement else 1) or 1
            used = per_placement.get(creative.placement_id, 0)
            if used >= cap:
                continue
            per_placement[creative.placement_id] = used + 1
            shown.append(creative.to_dict(language=language))

        # The answer depends on a header the caches do not key on, so it is
        # not a shared-cacheable response.
        return ({"items": shown, "total": len(shown), "path": path, "language": language},
                200, {"Cache-Control": "private, no-store"})


@ads_ns.route("/notices")
class NoticeFeed(Resource):
    @ads_ns.doc("serve_notices")
    @ads_ns.param("path", "The page being rendered")
    @ads_ns.param("language", "en or ne")
    @ads_ns.param("exclude", "Comma-separated notice codes the visitor has already dismissed")
    def get(self):
        """The live notices for a page, highest priority first.

        ``exclude`` exists because the dismissal is held by the frontend (see
        ``/notices/<id>/dismiss``): passing back what it already knows was
        dismissed means the bar never flashes up and disappears again. It is
        an optimisation, not a permission check — the notices are public
        either way, and a caller who omits it simply gets the full list.
        """
        path = site_path(request.args.get("path") or "/", "path", required=True)
        language = _language()

        raw_exclude = (request.args.get("exclude") or "")[:1000]
        excluded = {code.strip() for code in raw_exclude.split(",") if code.strip()}

        now = datetime.utcnow()
        notices = (SiteNotice.query
                   .filter(SiteNotice.is_active.is_(True))
                   .order_by(SiteNotice.priority.desc(), SiteNotice.id.asc())
                   .all())
        items = [n.to_dict(language=language) for n in notices
                 if n.is_live(now) and n.matches(path) and n.code not in excluded]

        return ({"items": items, "total": len(items), "path": path, "language": language},
                200, {"Cache-Control": "private, no-store"})


def _count_event(kind):
    """Shared body of /impression and /click.

    Answers 204 in every case that is not outright abuse, including an
    unknown creative id and a campaign that is not live. That is deliberate:
    a different answer for "that id exists" would turn a counter endpoint
    into an enumeration oracle, and there is nothing a caller can usefully
    do with the distinction anyway.
    """
    data = json_object()
    creative_id = _integer(data.get("creative_id"), "creative_id", minimum=1)
    path = site_path(data.get("path"), "path")

    verdict = throttle.allow(
        _visitor_bucket(), kind, creative_id,
        dedup_seconds=throttle.IMPRESSION_DEDUP_SECONDS if kind == "impression" else 0,
    )
    if verdict == throttle.RATE_LIMITED:
        raise TooManyRequests("Too many advert events from this visitor")
    if verdict == throttle.DUPLICATE:
        return _no_content()

    creative = db.session.get(AdCreative, creative_id)
    live = (creative is not None and creative.is_active
            and creative.campaign is not None and creative.campaign.is_live())
    if not live:
        # Counting against a finished or paused campaign is the easiest way
        # to corrupt a report after the fact, so it is refused quietly.
        return _no_content()

    if kind == "impression":
        creative.impressions = (creative.impressions or 0) + 1
    else:
        creative.clicks = (creative.clicks or 0) + 1
    db.session.commit()

    # Field names are the collector's declared vocabulary; it ignores
    # anything else rather than raising, and derives country and device
    # itself from the request.
    _record_analytics(f"ad_{kind}", path=path, ad_creative_id=creative.id,
                      utm_campaign=creative.campaign.utm_campaign)
    return _no_content()


@ads_ns.route("/impression")
class AdImpression(Resource):
    @ads_ns.doc("record_ad_impression")
    @ads_ns.expect(event_input)
    @ads_ns.response(204, "Recorded, or deliberately not recorded")
    @ads_ns.response(429, "Too many advert events from this visitor")
    @limiter.limit("60 per minute", key_func=rate_limit_key)
    def post(self):
        """Count that a creative was shown. Public, unauthenticated, cheap.

        An open write to a number that decides next year's campaign budget
        needs defending, so there are two layers and they guard different
        things. ``flask_limiter`` caps requests per address; the in-process
        window in ``throttle.py`` caps what gets *counted* and deduplicates
        the same visitor's repeat views of the same creative for half an
        hour. The consequence is worth stating plainly: an impression here
        means "a visitor saw this", not "it rendered", and a campaign's
        figure is therefore a reach number rather than a render count.

        One write, no read of anything the visitor owns, and no body in the
        response.
        """
        return _count_event("impression")


@ads_ns.route("/click")
class AdClick(Resource):
    @ads_ns.doc("record_ad_click")
    @ads_ns.expect(event_input)
    @ads_ns.response(204, "Recorded, or deliberately not recorded")
    @ads_ns.response(429, "Too many advert events from this visitor")
    @limiter.limit("60 per minute", key_func=rate_limit_key)
    def post(self):
        """Count that a creative was clicked.

        Rate limited like an impression but **not** deduplicated: a visitor
        who clicks the same offer twice has clicked twice, and suppressing
        the second would quietly understate the only figure that measures
        whether anybody wanted the offer.
        """
        return _count_event("click")


@ads_ns.route("/notices/<int:notice_id>/dismiss")
@ads_ns.param("notice_id", "The notice identifier")
class NoticeDismiss(Resource):
    @ads_ns.doc("dismiss_notice")
    @ads_ns.response(200, "Dismissal counted; the response says where to remember it")
    @ads_ns.response(409, "This notice is not dismissible")
    @limiter.limit("30 per minute", key_func=rate_limit_key)
    def post(self, notice_id):
        """Count a dismissal and tell the frontend where to remember it.

        **The dismissal is held by the frontend, not the server.** Two
        reasons, and the second is the binding one:

        1. Remembering it server-side needs a per-visitor row keyed on
           ``subject_key``, and ``subject_key`` only exists once a visitor
           has answered the consent banner. The visitors most likely to be
           annoyed by a reappearing bar are exactly the ones who have not
           answered it, so the server-side version would fail them first.
        2. Storing "this visitor dismissed that notice" against a visitor
           key is a preference about them, which needs ``PREFERENCES``
           consent. A design that only works for people who agreed to be
           remembered is not a design, it is a gap.

        So this endpoint increments the counter — an aggregate, tied to
        nobody — and returns where the browser should put the dismissal:

        ``store: "local"``
            ``PREFERENCES`` is granted, so persist for ``dismiss_days`` in
            ``localStorage``.
        ``store: "session"``
            It is not granted (or the notice is session-scoped), so hold it
            in ``sessionStorage``. Storage a visitor asked for by clicking
            "close" is carrying out their own request for the duration of
            that request; it is not a profile, and it is dropped when the
            tab closes.

        **The honest limitation:** with ``store: "session"`` the notice comes
        back on the visitor's next browsing session. It will not follow them
        around that session, which is what the brief requires, but it is not
        a durable "never show me this again" — and it cannot be made into
        one without either consent or an identifier, which is the point.
        """
        notice = db.session.get(SiteNotice, notice_id)
        if not notice:
            raise NotFound("Notice not found")
        if not notice.is_dismissible:
            raise Conflict("This notice cannot be dismissed")

        verdict = throttle.allow(_visitor_bucket(), "dismiss", notice_id,
                                 dedup_seconds=throttle.IMPRESSION_DEDUP_SECONDS)
        if verdict == throttle.RATE_LIMITED:
            raise TooManyRequests("Too many advert events from this visitor")
        if verdict == throttle.OK:
            notice.dismissals = (notice.dismissals or 0) + 1
            db.session.commit()
            _record_analytics("notice_dismiss", notice_id=notice.id)

        durable = bool(notice.dismiss_days) and allows(PREFERENCES)
        return {
            "code": notice.code,
            "dismiss_days": notice.dismiss_days if durable else 0,
            "store": "local" if durable else "session",
            "stored_server_side": False,
        }, 200


# ── Administration: campaigns ────────────────────────────────────────────────

_CAMPAIGN_FIELDS = ("code", "name", "objective", "occasion", "landing_path",
                    "utm_campaign", "starts_at", "ends_at")


def _load_campaign(campaign_id):
    campaign = db.session.get(AdCampaign, campaign_id)
    if not campaign:
        raise NotFound("Campaign not found")
    return campaign


def _apply_campaign(data, campaign=None):
    """Whitelisted write of the editable campaign fields.

    ``status`` is absent on purpose and rejected by name below, so a client
    that tries to set it is told where the lifecycle endpoints are rather
    than having it silently ignored.
    """
    if "status" in data:
        raise BadRequest(
            "Campaign status is not set directly. Use /campaigns/<id>/activate, "
            "/pause or /finish."
        )
    _reject_unknown(data, _CAMPAIGN_FIELDS)
    creating = campaign is None
    campaign = campaign or AdCampaign(status=AdCampaign.DRAFT)

    if creating or "code" in data:
        code = text(data.get("code"), "code", 60, required=True)
        campaign.code = _unique_code(AdCampaign, code, current=None if creating else campaign)
    if creating or "name" in data:
        campaign.name = text(data.get("name"), "name", 150, required=True)
    if "objective" in data:
        campaign.objective = text(data.get("objective") or "", "objective", 255) or None
    if "occasion" in data:
        campaign.occasion = text(data.get("occasion") or "", "occasion", 60) or None
    if "utm_campaign" in data:
        campaign.utm_campaign = text(data.get("utm_campaign") or "", "utm_campaign", 80) or None
    if "landing_path" in data:
        campaign.landing_path = site_path(data.get("landing_path"), "landing_path")
    if "starts_at" in data:
        campaign.starts_at = _moment(data.get("starts_at"), "starts_at")
    if "ends_at" in data:
        campaign.ends_at = _moment(data.get("ends_at"), "ends_at")

    if campaign.starts_at and campaign.ends_at and campaign.ends_at <= campaign.starts_at:
        raise BadRequest("'ends_at' must be after 'starts_at'")
    return campaign


@ads_ns.route("/campaigns")
class CampaignList(Resource):
    @ads_ns.doc("list_campaigns", security="Bearer")
    @ads_ns.param("status", "Filter by status")
    @ads_ns.param("occasion", "Filter by occasion, e.g. dashain")
    @ads_ns.param("live", "true to list only campaigns live right now")
    @require_roles("admin", "super_admin")
    def get(self):
        """Every campaign, paginated, newest first."""
        page, per_page = pagination()
        query = AdCampaign.query

        status = request.args.get("status")
        if status:
            if status not in AdCampaign.STATUSES:
                raise BadRequest(f"'status' must be one of {', '.join(AdCampaign.STATUSES)}")
            query = query.filter(AdCampaign.status == status)
        occasion = request.args.get("occasion")
        if occasion:
            query = query.filter(AdCampaign.occasion == occasion[:60])
        if (request.args.get("live") or "").lower() in ("1", "true", "yes"):
            now = datetime.utcnow()
            query = query.filter(
                AdCampaign.status == AdCampaign.RUNNING,
                db.or_(AdCampaign.starts_at.is_(None), AdCampaign.starts_at <= now),
                db.or_(AdCampaign.ends_at.is_(None), AdCampaign.ends_at >= now),
            )

        paginated = (query.order_by(AdCampaign.created_at.desc(), AdCampaign.id.desc())
                     .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "items": [c.to_dict() for c in paginated.items],
            "total": paginated.total,
            "page": page,
            "per_page": per_page,
            "pages": paginated.pages,
        }, 200

    @ads_ns.doc("create_campaign", security="Bearer")
    @ads_ns.expect(campaign_input)
    @require_roles("admin", "super_admin")
    def post(self):
        """Create a campaign. It starts as a draft and serves nothing."""
        from flask_jwt_extended import get_jwt_identity
        campaign = _apply_campaign(json_object())
        campaign.created_by = int(get_jwt_identity())
        db.session.add(campaign)
        db.session.commit()
        return campaign.to_dict(), 201


@ads_ns.route("/campaigns/<int:campaign_id>")
@ads_ns.param("campaign_id", "The campaign identifier")
@ads_ns.response(404, "Campaign not found")
class CampaignDetail(Resource):
    @ads_ns.doc("get_campaign", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, campaign_id):
        """One campaign and its creatives."""
        return _load_campaign(campaign_id).to_dict(include_creatives=True), 200

    @ads_ns.doc("update_campaign", security="Bearer")
    @ads_ns.expect(campaign_input)
    @require_roles("admin", "super_admin")
    def put(self, campaign_id):
        """Edit a campaign's copy, dates and landing path. Not its status."""
        campaign = _apply_campaign(json_object(), _load_campaign(campaign_id))
        db.session.commit()
        return campaign.to_dict(), 200

    @ads_ns.doc("delete_campaign", security="Bearer")
    @require_roles("admin", "super_admin")
    def delete(self, campaign_id):
        """Delete a campaign and its creatives.

        A running campaign is refused: deleting one is how an advert
        disappears from the live site with no record of why. Finish it
        first, which leaves the numbers intact for the report.
        """
        campaign = _load_campaign(campaign_id)
        if campaign.status == AdCampaign.RUNNING:
            raise Conflict("Finish or pause the campaign before deleting it")
        db.session.delete(campaign)
        db.session.commit()
        return _no_content()


#: Which statuses each lifecycle action may be reached from. Encoded once,
#: here, so "can a finished campaign be restarted" has a single answer.
_TRANSITIONS = {
    "activate": (AdCampaign.DRAFT, AdCampaign.SCHEDULED, AdCampaign.PAUSED),
    "pause": (AdCampaign.RUNNING, AdCampaign.SCHEDULED),
    "finish": (AdCampaign.DRAFT, AdCampaign.SCHEDULED, AdCampaign.RUNNING, AdCampaign.PAUSED),
}


def _transition(campaign, action):
    allowed = _TRANSITIONS[action]
    if campaign.status not in allowed:
        raise Conflict(
            f"A {campaign.status} campaign cannot be {action}d. "
            f"Allowed from: {', '.join(allowed)}."
        )
    if action == "activate":
        # A campaign whose window has not opened yet is 'scheduled', not
        # 'running': is_live() would say no either way, and the status
        # should not claim something is on the site when it is not.
        now = datetime.utcnow()
        if campaign.ends_at and campaign.ends_at <= now:
            raise BadRequest("This campaign's end date has passed; move it before activating")
        campaign.status = (AdCampaign.SCHEDULED
                           if campaign.starts_at and campaign.starts_at > now
                           else AdCampaign.RUNNING)
    elif action == "pause":
        campaign.status = AdCampaign.PAUSED
    else:
        campaign.status = AdCampaign.FINISHED
    db.session.commit()
    return campaign.to_dict()


@ads_ns.route("/campaigns/<int:campaign_id>/activate")
@ads_ns.param("campaign_id", "The campaign identifier")
class CampaignActivate(Resource):
    @ads_ns.doc("activate_campaign", security="Bearer")
    @ads_ns.response(409, "Not a status this can be reached from")
    @require_roles("admin", "super_admin")
    def post(self, campaign_id):
        """Put a campaign live, or schedule it if its start date is ahead."""
        return _transition(_load_campaign(campaign_id), "activate"), 200


@ads_ns.route("/campaigns/<int:campaign_id>/pause")
@ads_ns.param("campaign_id", "The campaign identifier")
class CampaignPause(Resource):
    @ads_ns.doc("pause_campaign", security="Bearer")
    @ads_ns.response(409, "Not a status this can be reached from")
    @require_roles("admin", "super_admin")
    def post(self, campaign_id):
        """Stop serving a campaign without ending it. Reversible."""
        return _transition(_load_campaign(campaign_id), "pause"), 200


@ads_ns.route("/campaigns/<int:campaign_id>/finish")
@ads_ns.param("campaign_id", "The campaign identifier")
class CampaignFinish(Resource):
    @ads_ns.doc("finish_campaign", security="Bearer")
    @ads_ns.response(409, "Already finished")
    @require_roles("admin", "super_admin")
    def post(self, campaign_id):
        """End a campaign. Terminal — reuse means a new campaign."""
        return _transition(_load_campaign(campaign_id), "finish"), 200


# ── Administration: placements ───────────────────────────────────────────────

_PLACEMENT_FIELDS = ("code", "name", "description", "recommended_width",
                     "recommended_height", "max_creatives", "is_active")


def _load_placement(placement_id):
    placement = db.session.get(AdPlacement, placement_id)
    if not placement:
        raise NotFound("Placement not found")
    return placement


def _apply_placement(data, placement=None):
    _reject_unknown(data, _PLACEMENT_FIELDS)
    creating = placement is None
    placement = placement or AdPlacement()

    if creating or "code" in data:
        code = text(data.get("code"), "code", 60, required=True)
        placement.code = _unique_code(AdPlacement, code, current=None if creating else placement)
    if creating or "name" in data:
        placement.name = text(data.get("name"), "name", 150, required=True)
    if "description" in data:
        placement.description = text(data.get("description") or "", "description", 300) or None
    for key in ("recommended_width", "recommended_height"):
        if key in data:
            value = data[key]
            setattr(placement, key, None if value is None
                    else _integer(value, key, minimum=1, maximum=10000))
    if "max_creatives" in data:
        placement.max_creatives = _integer(data["max_creatives"], "max_creatives",
                                           minimum=1, maximum=20)
    elif creating:
        placement.max_creatives = 1
    if "is_active" in data:
        placement.is_active = _boolean(data["is_active"], "is_active")
    elif creating:
        placement.is_active = True
    return placement


@ads_ns.route("/placements")
class PlacementList(Resource):
    @ads_ns.doc("list_placements", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """Every slot on the site, filled or not."""
        placements = AdPlacement.query.order_by(AdPlacement.code.asc()).all()
        return {"items": [p.to_dict() for p in placements], "total": len(placements)}, 200

    @ads_ns.doc("create_placement", security="Bearer")
    @ads_ns.expect(placement_input)
    @require_roles("admin", "super_admin")
    def post(self):
        """Add a slot. The frontend has to know the code to render it."""
        placement = _apply_placement(json_object())
        db.session.add(placement)
        db.session.commit()
        return placement.to_dict(), 201


@ads_ns.route("/placements/<int:placement_id>")
@ads_ns.param("placement_id", "The placement identifier")
@ads_ns.response(404, "Placement not found")
class PlacementDetail(Resource):
    @ads_ns.doc("get_placement", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, placement_id):
        """One slot."""
        return _load_placement(placement_id).to_dict(), 200

    @ads_ns.doc("update_placement", security="Bearer")
    @ads_ns.expect(placement_input)
    @require_roles("admin", "super_admin")
    def put(self, placement_id):
        """Edit a slot. Deactivating one stops everything in it serving."""
        placement = _apply_placement(json_object(), _load_placement(placement_id))
        db.session.commit()
        return placement.to_dict(), 200

    @ads_ns.doc("delete_placement", security="Bearer")
    @ads_ns.response(409, "Creatives still reference this placement")
    @require_roles("admin", "super_admin")
    def delete(self, placement_id):
        """Delete an empty slot.

        Refused while creatives point at it: the foreign key is not nullable,
        so the alternative is a failed commit or a cascade that silently
        deletes somebody's artwork.
        """
        placement = _load_placement(placement_id)
        if AdCreative.query.filter_by(placement_id=placement.id).count():
            raise Conflict("Move or delete the creatives in this placement first")
        db.session.delete(placement)
        db.session.commit()
        return _no_content()


# ── Administration: creatives ────────────────────────────────────────────────

_CREATIVE_FIELDS = ("placement_id", "format", "headline", "body", "call_to_action",
                    "target_path", "image_path", "image_alt", "headline_ne", "body_ne",
                    "page_paths", "countries", "priority", "is_active")


def _load_creative(creative_id):
    creative = db.session.get(AdCreative, creative_id)
    if not creative:
        raise NotFound("Creative not found")
    return creative


def _require_alt_text(creative):
    """An active creative carrying an image must describe it.

    The model's docstring states the rule; this is where it is actually
    true. An image that carries the offer is the content — a screen reader
    user with no alt text is simply not told there is an offer.
    """
    if creative.is_active and creative.image_path and not (creative.image_alt or "").strip():
        raise BadRequest(
            "A creative with an image cannot be activated without 'image_alt'. "
            "The image carries the offer, so it is content, not decoration."
        )
    return creative


def _apply_creative(data, campaign, creative=None):
    _reject_unknown(data, _CREATIVE_FIELDS)
    creating = creative is None
    creative = creative or AdCreative(campaign_id=campaign.id, is_active=True, priority=0)

    if creating or "placement_id" in data:
        placement_id = _integer(data.get("placement_id"), "placement_id", minimum=1)
        creative.placement = _load_placement(placement_id)
        creative.placement_id = placement_id
    if "format" in data:
        if data["format"] not in AdCreative.FORMATS:
            raise BadRequest(f"'format' must be one of {', '.join(AdCreative.FORMATS)}")
        creative.format = data["format"]
    if creating or "headline" in data:
        creative.headline = text(data.get("headline"), "headline", 150, required=True)
    if "body" in data:
        creative.body = text(data.get("body") or "", "body", 400) or None
    if "call_to_action" in data:
        creative.call_to_action = text(data.get("call_to_action") or "",
                                       "call_to_action", 60) or None
    if "headline_ne" in data:
        creative.headline_ne = text(data.get("headline_ne") or "", "headline_ne", 200) or None
    if "body_ne" in data:
        creative.body_ne = text(data.get("body_ne") or "", "body_ne", 500) or None
    if "target_path" in data:
        creative.target_path = site_path(data.get("target_path"), "target_path")
    if "image_path" in data:
        # Not a path on this site: an image may legitimately sit on a CDN.
        # It never reaches an href, so the open-redirect rule does not apply.
        creative.image_path = text(data.get("image_path") or "", "image_path", 500) or None
    if "image_alt" in data:
        creative.image_alt = text(data.get("image_alt") or "", "image_alt", 200) or None
    if "page_paths" in data:
        creative.page_paths = _path_list(data.get("page_paths"), "page_paths")
    if "countries" in data:
        creative.countries = _country_list(data.get("countries"))
    if "priority" in data:
        creative.priority = _integer(data["priority"], "priority", minimum=-1000, maximum=1000)
    if "is_active" in data:
        creative.is_active = _boolean(data["is_active"], "is_active")

    return _require_alt_text(creative)


@ads_ns.route("/campaigns/<int:campaign_id>/creatives")
@ads_ns.param("campaign_id", "The campaign identifier")
@ads_ns.response(404, "Campaign not found")
class CreativeList(Resource):
    @ads_ns.doc("list_creatives", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, campaign_id):
        """The creatives belonging to a campaign, highest priority first."""
        campaign = _load_campaign(campaign_id)
        creatives = sorted(campaign.creatives, key=lambda c: (-(c.priority or 0), c.id))
        return {"items": [c.to_dict() for c in creatives], "total": len(creatives),
                "campaign_id": campaign.id}, 200

    @ads_ns.doc("create_creative", security="Bearer")
    @ads_ns.expect(creative_input)
    @require_roles("admin", "super_admin")
    def post(self, campaign_id):
        """Add a creative to a campaign.

        Created active by default, so the alt-text rule is checked here too
        — otherwise "create it active with an image and no alt" would walk
        straight past the activation endpoint that enforces it.
        """
        campaign = _load_campaign(campaign_id)
        creative = _apply_creative(json_object(), campaign)
        db.session.add(creative)
        db.session.commit()
        return creative.to_dict(), 201


@ads_ns.route("/creatives/<int:creative_id>")
@ads_ns.param("creative_id", "The creative identifier")
@ads_ns.response(404, "Creative not found")
class CreativeDetail(Resource):
    @ads_ns.doc("get_creative", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, creative_id):
        """One creative, with its counters."""
        return _load_creative(creative_id).to_dict(), 200

    @ads_ns.doc("update_creative", security="Bearer")
    @ads_ns.expect(creative_input)
    @require_roles("admin", "super_admin")
    def put(self, creative_id):
        """Edit a creative."""
        creative = _load_creative(creative_id)
        _apply_creative(json_object(), creative.campaign, creative)
        db.session.commit()
        return creative.to_dict(), 200

    @ads_ns.doc("delete_creative", security="Bearer")
    @require_roles("admin", "super_admin")
    def delete(self, creative_id):
        """Delete a creative and its counters with it."""
        creative = _load_creative(creative_id)
        db.session.delete(creative)
        db.session.commit()
        return _no_content()


@ads_ns.route("/creatives/<int:creative_id>/activate")
@ads_ns.param("creative_id", "The creative identifier")
class CreativeActivate(Resource):
    @ads_ns.doc("activate_creative", security="Bearer")
    @ads_ns.response(400, "An image without alt text cannot be activated")
    @require_roles("admin", "super_admin")
    def post(self, creative_id):
        """Switch a creative on. Refused if it has an image and no alt text."""
        creative = _load_creative(creative_id)
        creative.is_active = True
        _require_alt_text(creative)
        db.session.commit()
        return creative.to_dict(), 200


@ads_ns.route("/creatives/<int:creative_id>/deactivate")
@ads_ns.param("creative_id", "The creative identifier")
class CreativeDeactivate(Resource):
    @ads_ns.doc("deactivate_creative", security="Bearer")
    @require_roles("admin", "super_admin")
    def post(self, creative_id):
        """Switch a creative off without deleting it or its numbers."""
        creative = _load_creative(creative_id)
        creative.is_active = False
        db.session.commit()
        return creative.to_dict(), 200


# ── Administration: notices ──────────────────────────────────────────────────

_NOTICE_FIELDS = ("code", "title", "body", "title_ne", "body_ne", "style", "tone",
                  "link_path", "link_label", "starts_at", "ends_at", "page_paths",
                  "dismiss_days", "max_impressions", "is_dismissible", "is_active",
                  "priority")


def _load_notice(notice_id):
    notice = db.session.get(SiteNotice, notice_id)
    if not notice:
        raise NotFound("Notice not found")
    return notice


def _apply_notice(data, notice=None):
    _reject_unknown(data, _NOTICE_FIELDS)
    creating = notice is None
    notice = notice or SiteNotice(is_active=False, priority=0)

    if creating or "code" in data:
        code = text(data.get("code"), "code", 60, required=True)
        notice.code = _unique_code(SiteNotice, code, current=None if creating else notice)
    if creating or "title" in data:
        notice.title = text(data.get("title"), "title", 150, required=True)
    if "body" in data:
        notice.body = text(data.get("body") or "", "body", 5000) or None
    if "title_ne" in data:
        notice.title_ne = text(data.get("title_ne") or "", "title_ne", 200) or None
    if "body_ne" in data:
        notice.body_ne = text(data.get("body_ne") or "", "body_ne", 5000) or None
    if "style" in data:
        if data["style"] not in SiteNotice.STYLES:
            raise BadRequest(f"'style' must be one of {', '.join(SiteNotice.STYLES)}")
        notice.style = data["style"]
    if "tone" in data:
        if data["tone"] not in SiteNotice.TONES:
            raise BadRequest(f"'tone' must be one of {', '.join(SiteNotice.TONES)}")
        notice.tone = data["tone"]
    if "link_path" in data:
        notice.link_path = site_path(data.get("link_path"), "link_path")
    if "link_label" in data:
        notice.link_label = text(data.get("link_label") or "", "link_label", 60) or None
    if "starts_at" in data:
        notice.starts_at = _moment(data.get("starts_at"), "starts_at")
    if "ends_at" in data:
        notice.ends_at = _moment(data.get("ends_at"), "ends_at")
    if "page_paths" in data:
        notice.page_paths = _path_list(data.get("page_paths"), "page_paths")
    if "dismiss_days" in data:
        notice.dismiss_days = _integer(data["dismiss_days"], "dismiss_days",
                                       minimum=0, maximum=3650)
    if "max_impressions" in data:
        notice.max_impressions = _integer(data["max_impressions"], "max_impressions",
                                          minimum=0, maximum=1000)
    if "is_dismissible" in data:
        notice.is_dismissible = _boolean(data["is_dismissible"], "is_dismissible")
    if "is_active" in data:
        notice.is_active = _boolean(data["is_active"], "is_active")
    if "priority" in data:
        notice.priority = _integer(data["priority"], "priority", minimum=-1000, maximum=1000)

    if notice.starts_at and notice.ends_at and notice.ends_at <= notice.starts_at:
        raise BadRequest("'ends_at' must be after 'starts_at'")
    return notice


@ads_ns.route("/admin/notices")
class AdminNoticeList(Resource):
    @ads_ns.doc("list_notices_admin", security="Bearer")
    @ads_ns.param("tone", "Filter by tone")
    @ads_ns.param("active", "true or false")
    @require_roles("admin", "super_admin")
    def get(self):
        """Every notice, live or not, paginated."""
        page, per_page = pagination()
        query = SiteNotice.query
        tone = request.args.get("tone")
        if tone:
            if tone not in SiteNotice.TONES:
                raise BadRequest(f"'tone' must be one of {', '.join(SiteNotice.TONES)}")
            query = query.filter(SiteNotice.tone == tone)
        active = (request.args.get("active") or "").lower()
        if active in ("1", "true", "yes"):
            query = query.filter(SiteNotice.is_active.is_(True))
        elif active in ("0", "false", "no"):
            query = query.filter(SiteNotice.is_active.is_(False))

        paginated = (query.order_by(SiteNotice.priority.desc(), SiteNotice.id.desc())
                     .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "items": [n.to_dict() for n in paginated.items],
            "total": paginated.total,
            "page": page,
            "per_page": per_page,
            "pages": paginated.pages,
        }, 200

    @ads_ns.doc("create_notice", security="Bearer")
    @ads_ns.expect(notice_input)
    @require_roles("admin", "super_admin")
    def post(self):
        """Create a notice. Inactive unless the payload says otherwise."""
        from flask_jwt_extended import get_jwt_identity
        notice = _apply_notice(json_object())
        notice.created_by = int(get_jwt_identity())
        db.session.add(notice)
        db.session.commit()
        return notice.to_dict(), 201


@ads_ns.route("/admin/notices/<int:notice_id>")
@ads_ns.param("notice_id", "The notice identifier")
@ads_ns.response(404, "Notice not found")
class AdminNoticeDetail(Resource):
    @ads_ns.doc("get_notice_admin", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self, notice_id):
        """One notice, including its dismissal count."""
        return _load_notice(notice_id).to_dict(), 200

    @ads_ns.doc("update_notice", security="Bearer")
    @ads_ns.expect(notice_input)
    @require_roles("admin", "super_admin")
    def put(self, notice_id):
        """Edit a notice."""
        notice = _apply_notice(json_object(), _load_notice(notice_id))
        db.session.commit()
        return notice.to_dict(), 200

    @ads_ns.doc("delete_notice", security="Bearer")
    @require_roles("admin", "super_admin")
    def delete(self, notice_id):
        """Delete a notice."""
        notice = _load_notice(notice_id)
        db.session.delete(notice)
        db.session.commit()
        return _no_content()


@ads_ns.route("/admin/notices/<int:notice_id>/activate")
@ads_ns.param("notice_id", "The notice identifier")
class AdminNoticeActivate(Resource):
    @ads_ns.doc("activate_notice", security="Bearer")
    @require_roles("admin", "super_admin")
    def post(self, notice_id):
        """Put a notice live. Its date window, if any, still applies."""
        notice = _load_notice(notice_id)
        notice.is_active = True
        db.session.commit()
        return notice.to_dict(), 200


@ads_ns.route("/admin/notices/<int:notice_id>/deactivate")
@ads_ns.param("notice_id", "The notice identifier")
class AdminNoticeDeactivate(Resource):
    @ads_ns.doc("deactivate_notice", security="Bearer")
    @require_roles("admin", "super_admin")
    def post(self, notice_id):
        """Take a notice down immediately, whatever its dates say."""
        notice = _load_notice(notice_id)
        notice.is_active = False
        db.session.commit()
        return notice.to_dict(), 200


# ── Reporting ────────────────────────────────────────────────────────────────

@ads_ns.route("/report")
class AdReport(Resource):
    @ads_ns.doc("ad_report", security="Bearer")
    @ads_ns.param("from", "ISO date; include campaigns that could have run on or after it")
    @ads_ns.param("to", "ISO date; include campaigns that could have run on or before it")
    @require_roles("admin", "super_admin")
    def get(self):
        """Impressions, clicks and CTR per campaign and per creative.

        **What the date range does, exactly.** The counters on a creative are
        lifetime totals — one column, incremented in place — so a range
        cannot slice them. It selects *which campaigns are in the report*:
        those whose period overlaps the range, with an undated campaign
        counting as always eligible because it was never time-bounded. A
        genuine per-day breakdown needs the analytics event log, which is a
        different module's table; saying so is better than returning a
        number shaped like a time series that is not one.
        """
        since = _moment(request.args.get("from"), "from")
        until = _moment(request.args.get("to"), "to")
        if since and until and until < since:
            raise BadRequest("'to' must not be before 'from'")

        query = AdCampaign.query
        if until:
            query = query.filter(db.or_(AdCampaign.starts_at.is_(None),
                                        AdCampaign.starts_at <= until))
        if since:
            query = query.filter(db.or_(AdCampaign.ends_at.is_(None),
                                        AdCampaign.ends_at >= since))

        rows = []
        total_impressions = total_clicks = 0
        for campaign in query.order_by(AdCampaign.created_at.desc(),
                                       AdCampaign.id.desc()).all():
            impressions = sum(c.impressions or 0 for c in campaign.creatives)
            clicks = sum(c.clicks or 0 for c in campaign.creatives)
            total_impressions += impressions
            total_clicks += clicks
            rows.append({
                "campaign_id": campaign.id,
                "code": campaign.code,
                "name": campaign.name,
                "status": campaign.status,
                "occasion": campaign.occasion,
                "starts_at": campaign.starts_at.isoformat() if campaign.starts_at else None,
                "ends_at": campaign.ends_at.isoformat() if campaign.ends_at else None,
                "impressions": impressions,
                "clicks": clicks,
                "click_through_rate": round(clicks / impressions * 100, 2) if impressions else 0.0,
                "creatives": [{
                    "creative_id": c.id,
                    "headline": c.headline,
                    "placement": c.placement.code if c.placement else None,
                    "is_active": c.is_active,
                    "impressions": c.impressions or 0,
                    "clicks": c.clicks or 0,
                    "click_through_rate": c.click_through_rate,
                } for c in sorted(campaign.creatives,
                                  key=lambda c: (-(c.impressions or 0), c.id))],
            })

        return {
            "items": rows,
            "total": len(rows),
            "from": since.isoformat() if since else None,
            "to": until.isoformat() if until else None,
            "totals": {
                "impressions": total_impressions,
                "clicks": total_clicks,
                "click_through_rate": (round(total_clicks / total_impressions * 100, 2)
                                       if total_impressions else 0.0),
            },
            "counters_are": "lifetime totals; the range selects campaigns, not days",
        }, 200
