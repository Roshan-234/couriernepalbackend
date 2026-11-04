"""Who is on the site, and what they have agreed to.

Every feature that writes something to a visitor's browser or records
something about their visit goes through here, so the consent rule is
enforced in one place rather than remembered in five.

**The visitor cookie is not set until a choice is made.** A first-time
visitor who has not touched the banner gets no identifier at all. That is
the difference between a site that asks and a site that asks after the
fact: the cookie that carries ``subject_key`` exists to remember the
decision, which makes it strictly necessary under the ePrivacy Directive —
but only once there is a decision to remember.

So three states, and the rest of the codebase only ever asks which one it
is in:

``unknown``
    No cookie, no record. Analytics may count the hit with no visitor key;
    nothing may be stored in the browser.
``decided``
    A record is in force. ``allows(category)`` answers for each purpose.
``stale``
    A record exists but the policy version moved or it aged out. Treated as
    ``unknown`` for permission, and the banner is shown again.
"""

from datetime import datetime, timedelta

from flask import current_app, request

from app.models.consent import (
    ANALYTICS,
    CONSENT_LIFETIME_DAYS,
    CURRENT_POLICY_VERSION,
    MARKETING,
    NECESSARY,
    PREFERENCES,
    ConsentRecord,
    hash_identifier,
)
from app.net import client_ip

#: Name kept short and unbranded; it appears in the cookie table of the
#: privacy policy, so it has to be something that can be described plainly.
VISITOR_COOKIE = "mf_vid"


def _cookie_kwargs():
    """Flags for every cookie this module sets.

    ``Secure`` is on everywhere except local development, where the site is
    served over plain HTTP and the browser would silently drop the cookie.
    """
    secure = not current_app.config.get("DEBUG", False)
    return {
        "httponly": True,
        "samesite": "Lax",
        "secure": secure,
        "path": "/",
    }


def subject_key():
    """The visitor's pseudonymous id, or None if they have not decided yet."""
    value = request.cookies.get(VISITOR_COOKIE)
    if not value or len(value) > 64 or not value.replace("-", "").replace("_", "").isalnum():
        return None
    return value


def set_visitor_cookie(response, key):
    """Attach the visitor cookie. Called only when a decision is recorded."""
    response.set_cookie(
        VISITOR_COOKIE, key,
        max_age=CONSENT_LIFETIME_DAYS * 24 * 3600,
        expires=datetime.utcnow() + timedelta(days=CONSENT_LIFETIME_DAYS),
        **_cookie_kwargs()
    )
    return response


def clear_visitor_cookie(response):
    response.delete_cookie(VISITOR_COOKIE, path="/")
    return response


def current_consent():
    """The consent decision in force for this request, or None."""
    key = subject_key()
    if not key:
        return None
    return ConsentRecord.current_for(key)


def allows(category):
    """May this request do something that needs `category` consent?

    Necessary processing never needs asking. Everything else defaults to no,
    which is the only safe default: a missing record is not agreement.
    """
    if category == NECESSARY:
        return True
    record = current_consent()
    return bool(record and record.allows(category))


def consent_state():
    """A description of where this visitor stands, for the banner to read."""
    key = subject_key()
    record = ConsentRecord.current_for(key) if key else None
    if record is None:
        # Distinguish "never asked" from "asked, but the answer went stale",
        # because the second one deserves different banner wording.
        had_any = bool(key and ConsentRecord.query.filter_by(subject_key=key).first())
        return {
            "state": "stale" if had_any else "unknown",
            "policy_version": CURRENT_POLICY_VERSION,
            "categories": [NECESSARY],
            "needs_decision": True,
            "allows": {NECESSARY: True, ANALYTICS: False,
                       MARKETING: False, PREFERENCES: False},
        }
    return {
        "state": "withdrawn" if record.action == ConsentRecord.WITHDRAWN else "decided",
        "policy_version": record.policy_version,
        "categories": record.categories or [NECESSARY],
        "needs_decision": False,
        "decided_at": record.created_at.isoformat() if record.created_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "allows": {c: record.allows(c) for c in
                   (NECESSARY, ANALYTICS, MARKETING, PREFERENCES)},
    }


def request_fingerprint():
    """The thin evidence bundle attached to a consent record or an event.

    A hashed address and a truncated user agent. Enough to show a decision
    came from a real session; not enough to identify anyone from the row.
    """
    return {
        "ip": client_ip(),
        "ip_hash": hash_identifier(client_ip()),
        "user_agent": (request.headers.get("User-Agent") or "")[:300] or None,
        "country": country_hint(),
    }


def country_hint():
    """Visitor country, if the edge told us. Never guessed from the address.

    Cloudflare and most CDNs set ``CF-IPCountry``. Without one, country is
    simply unknown — which is better than a wrong answer from a geo database
    that would also mean processing the address for a new purpose.
    """
    value = (request.headers.get("CF-IPCountry")
             or request.headers.get("X-Vercel-IP-Country") or "").strip().upper()
    if len(value) == 2 and value.isalpha() and value != "XX":
        return value
    return None


def device_hint():
    """Coarse device class from the user agent. Three buckets, nothing finer."""
    ua = (request.headers.get("User-Agent") or "").lower()
    if not ua:
        return None
    if "bot" in ua or "crawler" in ua or "spider" in ua:
        return "bot"
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    if "ipad" in ua or "tablet" in ua:
        return "tablet"
    return "desktop"
