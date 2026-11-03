"""API-key authentication for server-to-server merchant calls.

A browser session is the wrong credential for a storefront that books
shipments from its own backend: it expires, it carries CSRF baggage, and it
cannot be scoped. ``@require_api_key`` authenticates those calls instead,
against :class:`~app.models.api_key.MerchantApiKey`.

The lookup is by key *prefix*, which is indexed and unique. Scanning the
table and bcrypt-comparing every row would make one request cost as much as
the whole key table, which is a denial-of-service lever rather than a
security measure.

Failures are deliberately uniform. A caller cannot learn from the response
whether the prefix existed, whether the hash matched, whether the key had
expired or whether their IP was out of the allow-list -- all of those are the
same 401 with the same body, and a missing record still pays for one bcrypt
comparison so the timing does not leak either. Only a *valid* key presented
for an operation it was not granted gets the distinguishable 403, because at
that point the caller has already proved who they are.
"""

from datetime import datetime
from functools import wraps

from flask import g, request

from app.extensions import db
from app.models.api_key import MerchantApiKey

#: Header a merchant's server puts the key in.
API_KEY_HEADER = "X-API-Key"

#: One body for every authentication failure. See the module docstring.
_AUTH_FAILED = {"msg": "Invalid or missing API key"}

#: Hash used to burn a bcrypt comparison when no record matched, so that an
#: unknown prefix takes the same time as a known one. Built on first use
#: rather than at import so the cost is not paid by processes that never
#: serve an API-key request.
_DECOY_HASH = None


def _decoy_compare(presented):
    """Spend the same bcrypt round an existing key would have cost."""
    global _DECOY_HASH
    from app.extensions import bcrypt

    if _DECOY_HASH is None:
        _DECOY_HASH = bcrypt.generate_password_hash("decoy-no-such-key").decode("utf-8")
    try:
        bcrypt.check_password_hash(_DECOY_HASH, presented or "x")
    except (ValueError, TypeError):
        pass
    return None


def resolve_api_key(presented):
    """Return the matching usable key record, or ``None``.

    Every ``None`` path costs one bcrypt comparison.
    """
    prefix = MerchantApiKey.prefix_of(presented)
    if not prefix:
        return _decoy_compare(presented)

    record = MerchantApiKey.query.filter_by(key_prefix=prefix).first()
    if record is None:
        return _decoy_compare(presented)
    if not record.verify(presented):
        return None
    if not record.is_usable:
        return None
    if not record.allows_ip(request.remote_addr):
        return None
    return record


def _record_use(record):
    """Stamp the key with this call. Cheap enough to do inline for now."""
    record.last_used_at = datetime.utcnow()
    record.last_used_ip = request.remote_addr
    record.use_count = (record.use_count or 0) + 1
    db.session.commit()


def require_api_key(*scopes):
    """Gate a resource behind a merchant API key holding every ``scope``.

    On success the authenticated key and its merchant are on ``flask.g`` as
    ``g.api_key`` and ``g.merchant_id``; a route must filter its queries by
    ``g.merchant_id`` so a key can never reach another merchant's rows.
    """

    for scope in scopes:
        if scope not in MerchantApiKey.SCOPES:
            raise ValueError(f"Unknown API key scope: {scope}")

    def wrapper(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            record = resolve_api_key(request.headers.get(API_KEY_HEADER, ""))
            if record is None:
                return _AUTH_FAILED, 401

            missing = [s for s in scopes if not record.allows(s)]
            if missing:
                return {"msg": f"API key is missing scope: {', '.join(missing)}"}, 403

            _record_use(record)
            g.api_key = record
            g.merchant_id = record.merchant_id
            return fn(*args, **kwargs)

        return decorated

    return wrapper


def current_merchant_id():
    """The merchant behind the API key on this request, if any."""
    return getattr(g, "merchant_id", None)


def current_api_key():
    """The :class:`MerchantApiKey` authenticating this request, if any."""
    return getattr(g, "api_key", None)
