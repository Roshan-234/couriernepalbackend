from datetime import datetime
from flask import request, g
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from app.net import client_ip

# Paths to never log (to avoid infinite loops or noise)
SKIP_PATHS = ["/api/docs", "/api/health", "/static", "/favicon", "/api/swagger", "/api/content/pages", "/api/content/page"]

#: Public telemetry hit once or more on every page view: the analytics
#: beacon, advert serving and the impression and click counters.
#:
#: These are skipped for two reasons, and the second is the one that matters.
#: An audit row per beacon grows this table in proportion to *visitors*
#: rather than to administrative actions, burying the sign-ins and record
#: changes it exists to record. And the row would carry `client_ip()` — the
#: visitor's real address — alongside an analytics row that deliberately
#: stores only a salted hash, which quietly undoes that whole design and
#: contradicts what the published privacy page tells people.
#:
#: Anything added here must be genuinely high-volume, public and read-only
#: or a counter. An endpoint that changes a record belongs in the log.
HIGH_VOLUME_PATHS = (
    "/api/analytics/collect",
    "/api/ads/serve",
    "/api/ads/notices",
    "/api/ads/impression",
    "/api/ads/click",
)

#: Reads that every page performs, matched exactly rather than by prefix so
#: that the administrative endpoints underneath the same namespace — the
#: consent log and its summary — keep being audited. The *writes* on these
#: paths are real decisions by a person and are audited too; only the
#: polling is dropped.
POLLED_READ_PATHS = frozenset({
    "/api/consent", "/api/consent/", "/api/consent/policy",
})


def _derive_action(method, path):
    m = method.upper()
    if "/auth/login" in path:          return "LOGIN"
    if "/auth/register" in path:       return "REGISTER"
    if "/auth/logout" in path:         return "LOGOUT"
    if "/auth/password-reset" in path: return "PASSWORD_RESET"
    if "/auth/refresh" in path:        return "TOKEN_REFRESH"
    if "/auth/profile" in path:        return "VIEW_PROFILE"
    if "/shipments" in path:
        if m == "POST": return "CREATE_SHIPMENT"
        if m == "PUT":  return "UPDATE_SHIPMENT"
        if m == "DELETE": return "DELETE_SHIPMENT"
        return "VIEW_SHIPMENT"
    if "/tracking-providers" in path:
        if m == "POST":            return "CREATE_PROVIDER"
        if m in ("PUT", "PATCH"):  return "UPDATE_PROVIDER"
        if m == "DELETE":          return "DEACTIVATE_PROVIDER"
        return "VIEW_PROVIDER"
    if "/tracking" in path:
        if m == "POST": return "ADD_TRACKING_EVENT"
        return "TRACK_SHIPMENT"
    if "/users" in path:
        if m == "PUT":    return "UPDATE_USER"
        if m == "DELETE": return "DELETE_USER"
        return "VIEW_USER"
    if "/pricing" in path:   return "PRICING_QUERY"
    if "/audit" in path:     return "VIEW_AUDIT_LOG"
    if "/sessions" in path:
        if m == "DELETE": return "REVOKE_SESSION"
        return "VIEW_SESSIONS"
    if "/blog" in path:
        if m == "POST":   return "CREATE_BLOG_POST"
        if m in ("PUT", "PATCH"): return "UPDATE_BLOG_POST"
        return "VIEW_BLOG"
    return f"{m}_{path.strip('/').replace('/', '_').upper()[:50]}"


def _extract_resource(path):
    parts = [p for p in path.strip('/').split('/') if p and p != 'api']
    return parts[0] if parts else "unknown"


def audit_request(response):
    from app.extensions import db
    from app.models.audit_log import AuditLog
    try:
        path = request.path
        if any(skip in path for skip in SKIP_PATHS):
            return response
        if not path.startswith("/api"):
            return response
        if any(path.startswith(prefix) for prefix in HIGH_VOLUME_PATHS):
            return response
        if request.method in ("GET", "HEAD") and path in POLLED_READ_PATHS:
            return response
        # Traffic the edge turned away never reached the application, and this
        # hook runs before the rate limiter does. Logging it would let anyone
        # who can reach the host grow this table without limit.
        if getattr(g, 'rejected_at_edge', False):
            return response

        # Priority: explicit user_id set by handler (e.g. login before token exists)
        user_id = getattr(g, 'audit_user_id', None)

        if user_id is None:
            try:
                verify_jwt_in_request(optional=True)
                uid_str = get_jwt_identity()
                if uid_str:
                    user_id = int(uid_str)
            except Exception:
                pass

        action      = _derive_action(request.method, path)
        resource    = _extract_resource(path)

        # Extract resource_id from path (last numeric or long alphanumeric segment)
        parts = [p for p in path.strip('/').split('/') if p]
        resource_id = None
        for p in reversed(parts):
            if p.isdigit() or (len(p) > 8 and p.replace('-', '').isalnum()):
                resource_id = p
                break

        log = AuditLog(
            user_id=user_id,
            action=action,
            resource=resource,
            resource_id=resource_id,
            ip_address=client_ip(),
            user_agent=(request.user_agent.string or "")[:500] if request.user_agent else "",
            status_code=response.status_code,
            timestamp=datetime.utcnow(),
        )
        # Discard pending mutations left by a rejected request before logging it.
        db.session.rollback()
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()  # Leave the connection usable after logging fails.
    return response
