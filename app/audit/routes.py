from datetime import datetime
from flask import request
from flask_restx import Namespace, Resource
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.exceptions import BadRequest
from app.models.audit_log import AuditLog
from app.models.user import User
from app.extensions import db

audit_ns = Namespace("audit", description="Audit log access")

#: ``pagination()`` in app.validation caps per_page at 100. The audit log has
#: always allowed 200 — an admin scanning a day of activity needs the larger
#: page — so the bound is applied here instead of borrowing that helper.
MAX_PER_PAGE = 200


def _paging():
    """Read page/per_page from the query string, refusing junk with a 400."""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))
    except (TypeError, ValueError):
        raise BadRequest("page and per_page must be integers")
    if page < 1 or per_page < 1:
        raise BadRequest("page and per_page must be positive integers")
    return page, min(per_page, MAX_PER_PAGE)


def _timestamp(name):
    """Parse an ISO-8601 query argument, or None when it was not supplied."""
    value = request.args.get(name)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise BadRequest(f"{name} must be an ISO-8601 date, such as 2026-01-31 or 2026-01-31T14:30:00")


def _int_arg(name):
    value = request.args.get(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise BadRequest(f"{name} must be an integer")


def _search_arg(name, limit=200):
    value = request.args.get(name)
    if not value:
        return None
    if len(value) > limit:
        raise BadRequest(f"{name} must be at most {limit} characters")
    return value


def _page_response(query, page, per_page):
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)
    return {
        "logs": [l.to_dict() for l in paginated.items],
        "total": paginated.total,
        "page": paginated.page,
        "pages": paginated.pages,
        "per_page": per_page,
    }


@audit_ns.route("/logs/")
class LogList(Resource):
    @audit_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        """Get audit logs. Admins (admin or super_admin) see all; other users see their own."""
        uid = int(get_jwt_identity())
        user = db.session.get(User, uid)
        if not user:
            audit_ns.abort(404, "User not found")
        # is_admin covers both admin and super_admin, matching every other
        # module's require_roles("admin", "super_admin") gate.
        is_admin = user.is_admin

        page, per_page = _paging()
        action = _search_arg("action")
        resource = _search_arg("resource")
        date_from = _timestamp("date_from")
        date_to = _timestamp("date_to")
        filter_uid = _int_arg("user_id")

        q = AuditLog.query
        if not is_admin:
            q = q.filter_by(user_id=uid)
        elif filter_uid is not None:
            q = q.filter_by(user_id=filter_uid)

        if action:
            q = q.filter(AuditLog.action.ilike(f"%{action}%"))
        if resource:
            q = q.filter(AuditLog.resource.ilike(f"%{resource}%"))
        if date_from:
            q = q.filter(AuditLog.timestamp >= date_from)
        if date_to:
            q = q.filter(AuditLog.timestamp <= date_to)

        return _page_response(q.order_by(AuditLog.timestamp.desc()), page, per_page)


@audit_ns.route("/logs/me/")
class MyLogs(Resource):
    @audit_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        """Get the calling user's own audit logs."""
        uid = int(get_jwt_identity())
        page, per_page = _paging()
        action = _search_arg("action")
        q = AuditLog.query.filter_by(user_id=uid)
        if action:
            q = q.filter(AuditLog.action.ilike(f"%{action}%"))
        return _page_response(q.order_by(AuditLog.timestamp.desc()), page, per_page)
