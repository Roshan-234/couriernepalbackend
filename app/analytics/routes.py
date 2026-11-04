"""Website analytics: one public beacon, and reporting for administrators.

The public half is deliberately one endpoint and one shape. ``/collect``
accepts a declared event name and a bounded payload, writes a row, and
answers 204 with nothing in it. A beacon that returns data is a beacon that
can be used to ask questions, and nobody outside this building needs to ask
this table anything.

The admin half never shows the pseudonymous identifiers. ``visitor_key``
and ``ip_hash`` are what make counting possible; they are not what makes
browsing traffic useful, and putting them on a screen an operator can
scroll is how a pseudonymous table becomes a re-identifiable one. The
numbers are served from SQL aggregates, not from rows loaded into Python.

Nothing here reaches outside the process. Replacing a third-party analytics
script with a first-party one that still phones somewhere would miss the
point entirely.
"""

from datetime import date, datetime, time, timedelta

from flask import Response, request
from flask_restx import Namespace, Resource, fields
from sqlalchemy import case, distinct, func
from werkzeug.exceptions import BadRequest

from app.analytics.collect import compose_event
from app.auth.utils import require_roles
from app.extensions import db, limiter
from app.models.analytics import AnalyticsDaily, AnalyticsEvent
from app.net import rate_limit_key
from app.validation import json_object, pagination

analytics_ns = Namespace(
    "analytics",
    description="First-party website analytics: collection and reporting",
)

#: The events that mean a visitor did something the business cares about,
#: as opposed to merely reading a page. Reported separately on the overview
#: because these are the numbers anyone actually asks about.
CONVERSION_EVENTS = (
    AnalyticsEvent.QUOTE_REQUESTED,
    AnalyticsEvent.TRACKING_LOOKUP,
    AnalyticsEvent.SHIPMENT_BOOKED,
    AnalyticsEvent.CONTACT_SUBMITTED,
)

#: Default reporting window, and the longest one that may be asked for. The
#: cap is there because every query on this page is a full-range aggregate
#: and an unbounded ``from`` would be a cheap way to make the database work
#: very hard.
DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 366

#: How far back "realtime" looks. Long enough to show that a quiet site is
#: alive, short enough that the panel means "now".
REALTIME_MINUTES = 30

#: How many rows a top-N breakdown returns.
TOP_N = 10

#: A 1x1 transparent GIF, inline so the fallback needs no file on disk.
PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


collect_input = analytics_ns.model("AnalyticsCollectInput", {
    "name": fields.String(required=True, enum=list(AnalyticsEvent.NAMES),
                          description="One of the declared event names. Anything else is a 400."),
    "path": fields.String(description="Page path. A query string is stripped before storage."),
    "referrer": fields.String(description="Referring URL. Only its host is stored."),
    "utm_source": fields.String(),
    "utm_medium": fields.String(),
    "utm_campaign": fields.String(),
    "detail": fields.Raw(description="Up to 8 keys of short scalars. Nested objects are refused."),
})


# ── helpers ──────────────────────────────────────────────────────────────────

def _date_arg(name):
    value = request.args.get(name)
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise BadRequest(f"{name} must be an ISO date (YYYY-MM-DD)")


def _reporting_range():
    """The ``from``/``to`` window for a report, defaulted and bounded."""
    to_day = _date_arg("to") or datetime.utcnow().date()
    from_day = _date_arg("from") or (to_day - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    if from_day > to_day:
        raise BadRequest("'from' must not be after 'to'")
    if (to_day - from_day).days + 1 > MAX_RANGE_DAYS:
        raise BadRequest(f"A reporting range may cover at most {MAX_RANGE_DAYS} days")
    return from_day, to_day


def _bounds(from_day, to_day):
    return datetime.combine(from_day, time.min), datetime.combine(to_day, time.max)


def _top(column, start, end, *, extra=None, limit=TOP_N):
    """Top values of one column over the window, counted in SQL."""
    query = (db.session.query(column.label("value"), func.count(AnalyticsEvent.id).label("events"))
             .filter(AnalyticsEvent.occurred_at >= start,
                     AnalyticsEvent.occurred_at <= end,
                     column.isnot(None), column != ""))
    if extra is not None:
        query = query.filter(extra)
    rows = (query.group_by(column)
            .order_by(func.count(AnalyticsEvent.id).desc(), column.asc())
            .limit(limit).all())
    return [{"value": row.value, "events": row.events} for row in rows]


def _as_day(value):
    """``func.date`` gives a string on SQLite and a date on MySQL."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


# ── public collection ────────────────────────────────────────────────────────

@analytics_ns.route("/collect")
class Collect(Resource):
    @analytics_ns.doc("analytics_collect")
    @analytics_ns.expect(collect_input)
    @analytics_ns.response(204, "Recorded, or deliberately not. The caller is not told which.")
    @limiter.limit("120 per minute", key_func=rate_limit_key)
    def post(self):
        """Record one event from the browser.

        Public and unauthenticated, so the payload is treated as hostile:
        the event name must be one of the declared ones, ``detail`` is
        capped at a few short scalars, the referrer is reduced to its host
        and the path loses its query string. See ``app/analytics/collect.py``
        for each of those rules and why it is there.

        Whether the row carries a ``visitor_key`` depends only on whether
        the visitor agreed to analytics. Without consent the event is still
        written and still counted; it simply cannot be joined to any other
        event by the same person.

        The reply is 204 with no body, for every outcome including a dropped
        bot hit. An analytics beacon that reports back is an oracle, and
        there is nothing a caller gains by learning what we did with its hit.
        """
        data = json_object()
        name = data.get("name")
        if not isinstance(name, str) or name not in AnalyticsEvent.NAMES:
            raise BadRequest(
                "Unknown event name. Declared names: " + ", ".join(AnalyticsEvent.NAMES))

        event = compose_event(
            name,
            strict=True,
            path=data.get("path"),
            referrer=data.get("referrer"),
            utm_source=data.get("utm_source"),
            utm_medium=data.get("utm_medium"),
            utm_campaign=data.get("utm_campaign"),
            detail=data.get("detail"),
        )
        if event is not None:
            db.session.add(event)
            db.session.commit()
        return Response(status=204)


@analytics_ns.route("/collect.gif")
class CollectPixel(Resource):
    @analytics_ns.doc("analytics_collect_pixel")
    @analytics_ns.response(200, "A 1x1 transparent GIF, always.")
    @limiter.limit("120 per minute", key_func=rate_limit_key)
    def get(self):
        """No-JS fallback: an image whose request is the measurement.

        Parameters mirror the JSON endpoint (``name``, ``path``, ``ref``,
        ``utm_*``) but there is no ``detail``: a query string is the wrong
        place for a structured payload and this exists only to count page
        views where scripting is off.

        **The pixel never sets a cookie and never writes a visitor key**,
        even for a visitor who did consent. A ``<noscript>`` image fires from
        markup, with no way to check that the consent banner is in the state
        the server thinks it is, so the conservative read is the only one
        available: the hit is counted and nothing is attached to it.

        Always answers with the GIF, including for a rejected name or a bot,
        so that a broken page does not display a broken image.
        """
        name = (request.args.get("name") or AnalyticsEvent.PAGE_VIEW).strip()
        if name in AnalyticsEvent.NAMES:
            event = compose_event(
                name,
                strict=False,
                anonymous=True,
                path=request.args.get("path"),
                referrer=request.args.get("ref") or request.headers.get("Referer"),
                utm_source=request.args.get("utm_source"),
                utm_medium=request.args.get("utm_medium"),
                utm_campaign=request.args.get("utm_campaign"),
            )
            if event is not None:
                db.session.add(event)
                db.session.commit()
        return Response(PIXEL_GIF, mimetype="image/gif")


# ── administration ───────────────────────────────────────────────────────────

@analytics_ns.route("/overview")
class Overview(Resource):
    @analytics_ns.doc("analytics_overview", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """Dashboard figures for a date range, defaulting to the last 30 days.

        Query: ``from``, ``to`` (ISO dates, at most 366 days apart).

        **``unique_visitors`` is a floor, not a count.** Only visitors who
        agreed to analytics carry a key, so everyone who declined is in
        ``total_events`` and in ``events_without_visitor_key`` but can never
        be in this number — and two refusing visitors are indistinguishable
        from one. ``unique_visitors_is_floor`` is returned alongside it, and
        ``unique_visitors_source`` says whether the figure came from the raw
        events or, once those have been purged, from the daily rollup, where
        it is further approximated as the largest per-day figure summed over
        the range. Presenting it as a true audience size would be wrong by
        an unknown and variable margin, so do not.

        Every figure here is a SQL aggregate: no part of this endpoint loads
        the event table into Python.
        """
        from_day, to_day = _reporting_range()
        start, end = _bounds(from_day, to_day)
        window = (AnalyticsEvent.occurred_at >= start, AnalyticsEvent.occurred_at <= end)

        by_name = dict(
            db.session.query(AnalyticsEvent.name, func.count(AnalyticsEvent.id))
            .filter(*window).group_by(AnalyticsEvent.name).all()
        )
        total_events = sum(by_name.values())

        unique_visitors = db.session.query(
            func.count(distinct(AnalyticsEvent.visitor_key))).filter(*window).scalar() or 0
        anonymous_events = db.session.query(func.count(AnalyticsEvent.id)).filter(
            *window, AnalyticsEvent.visitor_key.is_(None)).scalar() or 0
        source = "events"

        if total_events == 0:
            # Events for this range have been purged, or never existed. The
            # rollup survives retention, so fall back to it and say so: the
            # sum of per-day maxima is the closest honest figure it supports.
            per_day = (db.session.query(func.max(AnalyticsDaily.visitors).label("v"))
                       .filter(AnalyticsDaily.day >= from_day, AnalyticsDaily.day <= to_day)
                       .group_by(AnalyticsDaily.day).subquery())
            rolled = db.session.query(func.coalesce(func.sum(per_day.c.v), 0)).scalar() or 0
            if rolled:
                unique_visitors, source = int(rolled), "daily_rollup"

        day_expr = func.date(AnalyticsEvent.occurred_at)
        series_rows = (
            db.session.query(
                day_expr.label("day"),
                func.count(AnalyticsEvent.id).label("events"),
                func.sum(case((AnalyticsEvent.name == AnalyticsEvent.PAGE_VIEW, 1),
                              else_=0)).label("page_views"),
                func.count(distinct(AnalyticsEvent.visitor_key)).label("visitors"),
            ).filter(*window).group_by(day_expr).order_by(day_expr).all()
        )
        measured = {_as_day(row.day): row for row in series_rows}
        # Charting wants a point per day, including the quiet ones.
        series = []
        cursor = from_day
        while cursor <= to_day:
            key = cursor.isoformat()
            row = measured.get(key)
            series.append({
                "day": key,
                "events": int(row.events) if row else 0,
                "page_views": int(row.page_views or 0) if row else 0,
                "visitors": int(row.visitors) if row else 0,
            })
            cursor += timedelta(days=1)

        devices = (db.session.query(AnalyticsEvent.device, func.count(AnalyticsEvent.id))
                   .filter(*window).group_by(AnalyticsEvent.device).all())

        return {
            "from": from_day.isoformat(),
            "to": to_day.isoformat(),
            "days": (to_day - from_day).days + 1,
            "total_events": total_events,
            "page_views": by_name.get(AnalyticsEvent.PAGE_VIEW, 0),
            "unique_visitors": int(unique_visitors),
            "unique_visitors_is_floor": True,
            "unique_visitors_source": source,
            "unique_visitors_basis": (
                "Consented traffic only. Visitors who declined analytics are counted "
                "in total_events but carry no key, so they cannot appear here and "
                "cannot be told apart from one another. Treat this as a lower bound."
            ),
            "events_without_visitor_key": anonymous_events,
            "events_by_name": {name: by_name.get(name, 0) for name in AnalyticsEvent.NAMES},
            "conversions": {name: by_name.get(name, 0) for name in CONVERSION_EVENTS},
            "series": series,
            "top_paths": _top(AnalyticsEvent.path, start, end,
                              extra=AnalyticsEvent.name == AnalyticsEvent.PAGE_VIEW),
            "top_referrer_hosts": _top(AnalyticsEvent.referrer_host, start, end),
            "top_countries": _top(AnalyticsEvent.country, start, end),
            "devices": {(device or "unknown"): count for device, count in devices},
        }, 200


@analytics_ns.route("/events")
class Events(Resource):
    @analytics_ns.doc("analytics_events", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """Raw events, paginated and filtered.

        Filters: ``name``, ``path``, ``from``, ``to`` (ISO dates),
        ``country``, ``utm_campaign``.

        Serialised through ``AnalyticsEvent.to_dict()``, which omits
        ``visitor_key``, ``session_key`` and ``ip_hash`` — and that omission
        is the point of this endpoint rather than an oversight. An operator
        looking at traffic needs to know what was read and from where; the
        pseudonymous keys add nothing to that and a listing that shows them
        is a listing that lets someone match a visit to a person they can
        already identify by other means. Aggregation uses the keys in SQL,
        where no one reads them.
        """
        page, per_page = pagination()
        query = AnalyticsEvent.query

        if name := request.args.get("name"):
            if name not in AnalyticsEvent.NAMES:
                raise BadRequest("Unknown event name. Declared names: "
                                 + ", ".join(AnalyticsEvent.NAMES))
            query = query.filter(AnalyticsEvent.name == name)
        if path := request.args.get("path"):
            query = query.filter(AnalyticsEvent.path == path[:300])
        if country := request.args.get("country"):
            query = query.filter(AnalyticsEvent.country == country.strip().upper()[:2])
        if campaign := request.args.get("utm_campaign"):
            query = query.filter(AnalyticsEvent.utm_campaign == campaign[:80])
        if from_day := _date_arg("from"):
            query = query.filter(AnalyticsEvent.occurred_at >= datetime.combine(from_day, time.min))
        if to_day := _date_arg("to"):
            query = query.filter(AnalyticsEvent.occurred_at <= datetime.combine(to_day, time.max))

        page_obj = (query.order_by(AnalyticsEvent.occurred_at.desc(), AnalyticsEvent.id.desc())
                    .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "items": [event.to_dict() for event in page_obj.items],
            "total": page_obj.total,
            "page": page,
            "per_page": per_page,
            "pages": page_obj.pages,
        }, 200


@analytics_ns.route("/daily")
class Daily(Resource):
    @analytics_ns.doc("analytics_daily", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """The rollup series, paginated.

        Filters: ``from``, ``to``, ``name``, ``path``.

        These rows outlive the events they came from, which is the whole
        reason they exist: retention deletes the visits, the trend stays.
        They hold no visitor key and are not personal data.
        """
        page, per_page = pagination()
        from_day, to_day = _reporting_range()
        query = AnalyticsDaily.query.filter(AnalyticsDaily.day >= from_day,
                                            AnalyticsDaily.day <= to_day)
        if name := request.args.get("name"):
            query = query.filter(AnalyticsDaily.name == name[:40])
        if path := request.args.get("path"):
            query = query.filter(AnalyticsDaily.path == path[:300])

        totals = query.with_entities(
            func.coalesce(func.sum(AnalyticsDaily.events), 0),
            func.coalesce(func.sum(AnalyticsDaily.visitors), 0),
        ).one()

        page_obj = (query.order_by(AnalyticsDaily.day.desc(), AnalyticsDaily.name.asc())
                    .paginate(page=page, per_page=per_page, error_out=False))
        return {
            "items": [row.to_dict() for row in page_obj.items],
            "total": page_obj.total,
            "page": page,
            "per_page": per_page,
            "pages": page_obj.pages,
            "from": from_day.isoformat(),
            "to": to_day.isoformat(),
            "sum_events": int(totals[0]),
            # Summed across slices, so the same visitor is counted once per
            # (name, path) they touched. A sum of distinct counts is not a
            # distinct count, and this one is not pretending to be.
            "sum_visitor_slices": int(totals[1]),
        }, 200


@analytics_ns.route("/realtime")
class Realtime(Resource):
    @analytics_ns.doc("analytics_realtime", security="Bearer")
    @require_roles("admin", "super_admin")
    def get(self):
        """The last 30 minutes, for the "is the site alive" panel.

        Counts come from SQL; the sample of recent events is capped and uses
        the same identifier-free serialisation as ``/events``.
        """
        since = datetime.utcnow() - timedelta(minutes=REALTIME_MINUTES)
        window = (AnalyticsEvent.occurred_at >= since,)

        by_name = dict(db.session.query(AnalyticsEvent.name, func.count(AnalyticsEvent.id))
                       .filter(*window).group_by(AnalyticsEvent.name).all())
        paths = (db.session.query(AnalyticsEvent.path, func.count(AnalyticsEvent.id).label("events"))
                 .filter(*window, AnalyticsEvent.path.isnot(None))
                 .group_by(AnalyticsEvent.path)
                 .order_by(func.count(AnalyticsEvent.id).desc(), AnalyticsEvent.path.asc())
                 .limit(TOP_N).all())
        recent = (AnalyticsEvent.query.filter(*window)
                  .order_by(AnalyticsEvent.occurred_at.desc(), AnalyticsEvent.id.desc())
                  .limit(50).all())

        return {
            "since": since.isoformat(),
            "minutes": REALTIME_MINUTES,
            "total_events": sum(by_name.values()),
            "events_by_name": by_name,
            "active_paths": [{"value": path, "events": events} for path, events in paths],
            "items": [event.to_dict() for event in recent],
            "total": len(recent),
        }, 200
