"""Working out who is actually calling.

The API sits behind the frontend's same-origin ``/api`` proxy, so
``request.remote_addr`` is the proxy for every request. Left alone that gives
one rate-limit bucket for the entire internet — ten junk sign-ins a minute
would lock out every customer — and an audit log in which every action came
from the same address.

``ProxyFix`` rewrites ``remote_addr`` from ``X-Forwarded-For``, but only as
many hops as ``TRUSTED_PROXY_HOPS`` says to trust. That setting matters: a
header is attacker-controlled unless a proxy you run overwrote it, so trusting
one that is not there lets a caller forge any address it likes.
"""

from flask import current_app, request


#: Set by the frontend's /api middleware, which overwrites rather than appends
#: it. Trusted only on a request that already proved it came from that proxy
#: by presenting the shared backend key — so the trust rests on a secret, not
#: on counting hops in a header anyone can write.
REAL_IP_HEADER = "X-Real-Client-IP"


def client_ip():
    """The caller's address, as well as it can be known.

    Three cases, in order of how much they can be trusted:

    1. The frontend proxy authenticated itself with the backend key and told
       us the visitor's address. That is the honest answer.
    2. ``TRUSTED_PROXY_HOPS`` is configured, so ``ProxyFix`` has already
       rewritten ``remote_addr`` from the forwarded chain.
    3. Neither, so the peer address — which behind a proxy is the proxy.
    """
    try:
        secret = current_app.config.get("BACKEND_PROXY_SECRET")
    except RuntimeError:
        secret = None
    if secret:
        forwarded = (request.headers.get(REAL_IP_HEADER) or "").strip()
        # A bare sanity check: this is a label for logs and rate-limit
        # buckets, not something that is parsed or interpolated anywhere.
        if forwarded and len(forwarded) <= 45:
            return forwarded
    return request.remote_addr or "unknown"


def rate_limit_key():
    """The bucket a request counts against.

    A signed-in caller is limited per account, which survives a shared proxy
    address and is the only thing that works when every request appears to
    come from one host. Anonymous traffic falls back to the address, which
    behind an unconfigured proxy is shared — hence the per-account lockout in
    the login handler, which does not depend on the address at all.
    """
    from flask_jwt_extended import get_jwt_identity
    from flask_jwt_extended.exceptions import JWTExtendedException
    from jwt import PyJWTError

    try:
        identity = get_jwt_identity()
    except (JWTExtendedException, PyJWTError, RuntimeError, KeyError):
        identity = None
    if identity:
        return f"user:{identity}"
    return f"ip:{client_ip()}"


def proxy_is_trusted():
    """True when the deployment told us a proxy sits in front."""
    try:
        return bool(current_app.config.get("TRUSTED_PROXY_HOPS"))
    except RuntimeError:
        return False
