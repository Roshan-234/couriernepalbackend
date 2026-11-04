"""Turning something that happened into an analytics row.

This module is separate from ``routes.py`` for two reasons.

The practical one: advertising and marketing log events as a side effect
of their own work, and ``from app.analytics.collect import record_event``
should not drag a Flask-RESTX namespace, its Swagger models and the whole
HTTP layer into a module that only wants to count an impression.

The important one: **the consent rule is stated once, here.** Exactly one
function in the codebase decides whether an event carries a visitor key
(:func:`visitor_identity`), and both the public endpoint and the in-process
helper go through it. Re-deriving that decision at a call site is how a
module ends up quietly writing an identifier for someone who refused —
which is the specific failure this whole package exists to avoid.

Everything a client sends is treated as hostile: ``/collect`` is public and
unauthenticated, so a payload is capped, flattened, truncated and stripped
of its query strings before it is anywhere near the database.
"""

import math
from datetime import datetime
from urllib.parse import urlsplit

from flask import current_app, request
from werkzeug.exceptions import BadRequest

from app.extensions import db
from app.models.analytics import AnalyticsEvent
from app.models.consent import ANALYTICS, hash_identifier
from app.visitor import allows, country_hint, device_hint, request_fingerprint, subject_key

#: A declared payload is a handful of named facts about one event ("which
#: quote form", "which tracking number shape"). Past that it is a log of
#: whatever the client felt like sending, which is what the column comment
#: on ``AnalyticsEvent.detail`` rules out.
MAX_DETAIL_KEYS = 8
MAX_DETAIL_KEY_LENGTH = 40
MAX_DETAIL_VALUE_LENGTH = 120

#: Beyond this an integer is not a count of anything, it is someone probing
#: what the JSON column will swallow.
MAX_DETAIL_INT = 10 ** 15

#: How long events from one visitor are treated as the same visit. A fixed
#: wall-clock window rather than a sliding one, so it needs no state and no
#: second cookie: the key is derived, never stored in the browser. The cost
#: is that a visit straddling the boundary counts as two, which is a fine
#: trade for not writing anything extra to someone's machine.
SESSION_WINDOW_MINUTES = 30

#: Fields :func:`record_event` accepts. Anything else is ignored rather than
#: raising, because this helper's contract is that it never breaks its caller.
ACCEPTED_FIELDS = (
    "path", "referrer", "referrer_host", "utm_source", "utm_medium",
    "utm_campaign", "detail", "country", "device", "ad_creative_id",
    "notice_id", "occurred_at",
)


def _width(column):
    """The declared width of a string column on ``AnalyticsEvent``.

    Read off the model rather than repeated as a constant here, so that
    widening a column cannot leave a truncation rule behind at the old size.
    """
    return AnalyticsEvent.__table__.c[column].type.length


def clean_path(value, strict=False):
    """Reduce a page reference to its path, with no query string.

    A query string on an internal URL carries exactly the risk that the
    referring URL does — ``/track?number=...``, ``/search?q=...`` — so only
    the path survives. An absolute URL is accepted and reduced the same way,
    because that is what ``location.href`` gives a careless beacon.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        if strict:
            raise BadRequest("path must be a string")
        return None
    value = value.strip()
    if not value:
        return None
    try:
        path = urlsplit(value).path or "/"
    except ValueError:
        if strict:
            raise BadRequest("path is not a valid URL path")
        return None
    if not path.startswith("/"):
        path = "/" + path
    return path[:_width("path")]


def clean_referrer_host(referrer, strict=False):
    """Keep the host a visitor arrived from, and nothing else.

    The full referring URL is not stored and never has been: a search engine
    puts the query in it, and a private page on another site puts its path
    in it. Neither is ours to hold, and the host alone answers the only
    question reporting asks — "where did this traffic come from".

    A relative or unparseable referrer yields ``None`` rather than an error:
    the browser supplies this header, and a malformed one is not worth
    failing a beacon over.
    """
    if referrer is None:
        return None
    if not isinstance(referrer, str):
        if strict:
            raise BadRequest("referrer must be a string")
        return None
    referrer = referrer.strip()
    if not referrer:
        return None
    try:
        parts = urlsplit(referrer)
    except ValueError:
        return None
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    return host[:_width("referrer_host")]


def clean_detail(raw, strict=False):
    """Flatten and bound the declared payload on an event.

    Scalars only, a handful of keys, short strings. Nested objects and
    arrays are refused outright: the moment ``detail`` can hold a structure,
    the table stops being analytics and becomes an unbounded document store
    that nobody has a retention rule for.

    ``strict`` is what the public endpoint uses — a client sending something
    it was not offered should be told, with a 400, rather than having its
    payload silently trimmed. The in-process helper is lenient instead,
    because dropping one bad field is better than losing the event.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        if strict:
            raise BadRequest("detail must be a JSON object")
        return None
    if len(raw) > MAX_DETAIL_KEYS:
        if strict:
            raise BadRequest(f"detail may carry at most {MAX_DETAIL_KEYS} keys")
        raw = dict(list(raw.items())[:MAX_DETAIL_KEYS])

    cleaned = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_DETAIL_KEY_LENGTH:
            if strict:
                raise BadRequest(
                    f"detail keys must be strings of 1-{MAX_DETAIL_KEY_LENGTH} characters")
            continue
        key = key.strip()

        if value is None or isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, int):
            if abs(value) > MAX_DETAIL_INT:
                if strict:
                    raise BadRequest(f"detail value for '{key}' is out of range")
                continue
            cleaned[key] = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                if strict:
                    raise BadRequest(f"detail value for '{key}' must be a finite number")
                continue
            cleaned[key] = value
        elif isinstance(value, str):
            cleaned[key] = value[:MAX_DETAIL_VALUE_LENGTH]
        else:
            if strict:
                raise BadRequest(
                    f"detail value for '{key}' must be a string, number or boolean; "
                    "nested objects and arrays are not accepted")
            continue
    return cleaned or None


def visitor_identity():
    """The visitor and session keys for this request, or ``(None, None)``.

    **The load-bearing rule of this package.** An event is written whether
    or not the visitor agreed to analytics — a page view is counted either
    way — but it only carries an identifier when ``allows(ANALYTICS)`` says
    so. Without one the row aggregates into totals and cannot be strung
    into a journey, which is the whole distinction the consent banner is
    promising.

    Consent is asked of :mod:`app.visitor` and nowhere else. The visitor key
    stored is the same ``subject_key`` the consent record uses, deliberately:
    an erasure request arrives with that key, and analytics rows have to be
    findable by it.

    The session key is derived, not stored, so remembering a visit costs the
    browser nothing extra.
    """
    try:
        if not allows(ANALYTICS):
            return None, None
        key = subject_key()
    except RuntimeError:
        # No request context: a CLI job or a background task. There is no
        # visitor, so there is certainly no consent.
        return None, None
    if not key:
        return None, None

    now = datetime.utcnow()
    window = now.replace(
        minute=(now.minute // SESSION_WINDOW_MINUTES) * SESSION_WINDOW_MINUTES,
        second=0, microsecond=0,
    )
    session = hash_identifier(f"{key}|{window.isoformat()}")
    return key[:64], (session[:64] if session else None)


def _request_traits():
    """Country, device class and hashed address for the current request.

    Only ``ip_hash`` is taken from the fingerprint. The raw address is in
    that dict because consent evidence hashes it there, and it is read here
    for exactly long enough to be hashed and discarded — it is never a field
    on an analytics row.
    """
    try:
        request.path
    except RuntimeError:
        return {"ip_hash": None, "country": None, "device": None}
    return {
        "ip_hash": request_fingerprint()["ip_hash"],
        "country": country_hint(),
        "device": device_hint(),
    }


def compose_event(name, *, strict=False, anonymous=False, path=None, referrer=None,
                  referrer_host=None, utm_source=None, utm_medium=None,
                  utm_campaign=None, detail=None, country=None, device=None,
                  ad_creative_id=None, notice_id=None, occurred_at=None):
    """Build an unsaved :class:`AnalyticsEvent`, or ``None`` to drop the hit.

    Returns ``None`` for traffic that should not be counted at all, which
    today means anything :func:`device_hint` calls a bot. A crawler hitting
    every page is not an audience, and counting it makes every number on the
    dashboard wrong in the same invisible direction.

    ``anonymous`` forces the row to carry no keys whatever the consent record
    says. It exists for the no-JS pixel, which has no way to be sure the
    request came from a page the visitor is actually looking at; the flag can
    only ever remove an identifier, never add one.
    """
    if name not in AnalyticsEvent.NAMES:
        raise BadRequest(
            "Unknown event name. An open event vocabulary turns this table "
            f"into an unbounded log; declared names are: {', '.join(AnalyticsEvent.NAMES)}")

    traits = _request_traits()
    device = device or traits["device"]
    if device == "bot":
        return None

    visitor_key, session_key = (None, None) if anonymous else visitor_identity()

    def clip(value, column):
        if value is None:
            return None
        if not isinstance(value, str):
            if strict:
                raise BadRequest(f"{column} must be a string")
            return None
        value = value.strip()
        return value[:_width(column)] or None

    country = (country or traits["country"] or None)
    if country:
        country = str(country).strip().upper()[:2] or None

    return AnalyticsEvent(
        name=name,
        visitor_key=visitor_key,
        session_key=session_key,
        path=clean_path(path, strict=strict),
        referrer_host=(clip(referrer_host, "referrer_host")
                       or clean_referrer_host(referrer, strict=strict)),
        utm_source=clip(utm_source, "utm_source"),
        utm_medium=clip(utm_medium, "utm_medium"),
        utm_campaign=clip(utm_campaign, "utm_campaign"),
        country=country,
        device=clip(device, "device"),
        # The address itself is not a column on this model and must not
        # become one; the hash is what makes country and bot filtering
        # possible without holding personal data.
        ip_hash=traits["ip_hash"],
        detail=clean_detail(detail, strict=strict),
        ad_creative_id=ad_creative_id if isinstance(ad_creative_id, int) else None,
        notice_id=notice_id if isinstance(notice_id, int) else None,
        occurred_at=occurred_at if isinstance(occurred_at, datetime) else datetime.utcnow(),
    )


def record_event(name, commit=True, **fields):
    """Log an analytics event from inside the backend. Never raises.

    The point of this helper is that a module can call it on the way past
    without wrapping it in a try block: an analytics write is a side effect,
    and a side effect must not take down the thing it is attached to. A bad
    payload, a locked table, a missing column — all of it ends as a warning
    in the log and a ``None`` return.

    The insert is wrapped in a SAVEPOINT so that if it fails it unwinds
    alone, leaving whatever the caller already has pending in the session
    untouched.

    Accepted fields: ``path``, ``referrer`` (reduced to its host) or
    ``referrer_host``, ``utm_source``, ``utm_medium``, ``utm_campaign``,
    ``detail``, ``country``, ``device``, ``ad_creative_id``, ``notice_id``,
    ``occurred_at``. Unknown names are ignored.

    There is deliberately no ``visitor_key`` argument. Whether an event
    carries an identifier is decided by :func:`visitor_identity` from the
    consent record and by nothing else, so that no caller can set one for a
    visitor who refused.

    :param commit: ``True`` (the default) writes immediately, which suits a
        fire-and-forget call. **A caller in the middle of its own
        transaction should pass ``False``** and let its own commit carry the
        row, rather than having this helper commit its half-finished work.
    :returns: the saved event, or ``None`` if it was dropped or failed.
    """
    try:
        unknown = [key for key in fields if key not in ACCEPTED_FIELDS]
        for key in unknown:
            fields.pop(key)
        if unknown:
            _warn("analytics: ignoring unknown record_event fields: %s", ", ".join(sorted(unknown)))

        event = compose_event(name, strict=False, **fields)
        if event is None:
            return None

        with db.session.begin_nested():
            db.session.add(event)
    except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
        _warn("analytics: dropped a %s event: %s", name, exc)
        return None

    if not commit:
        return event
    try:
        db.session.commit()
        return event
    except Exception as exc:  # noqa: BLE001
        # A failed commit leaves the session unusable, so it has to be rolled
        # back even though that is the one case where this helper can cost
        # its caller something. Callers inside a transaction pass commit=False.
        _warn("analytics: could not commit a %s event: %s", name, exc)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def _warn(message, *args):
    """Log without assuming there is an application to log through."""
    try:
        current_app.logger.warning(message, *args)
    except Exception:  # noqa: BLE001
        pass
