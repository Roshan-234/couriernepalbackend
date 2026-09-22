from datetime import datetime
from flask import request, g
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

# Paths to never log (to avoid infinite loops or noise)
SKIP_PATHS = ["/api/docs", "/api/health", "/static", "/favicon", "/api/swagger"]


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
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500] if request.user_agent else "",
            status_code=response.status_code,
            timestamp=datetime.utcnow(),
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        pass  # Never let audit logging break the actual response
    return response
