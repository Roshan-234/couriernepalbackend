from flask import request
from flask_restx import Namespace, Resource
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.models.audit_log import AuditLog
from app.models.user import User
from app.auth.utils import require_roles

audit_ns = Namespace("audit", description="Audit log access")

@audit_ns.route("/logs/")
class LogList(Resource):
    @audit_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        """Get audit logs. Admin sees all; users see own."""
        uid = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        roles = [r.name for r in user.roles]
        is_admin = "super_admin" in roles

        page      = int(request.args.get("page", 1))
        per_page  = min(int(request.args.get("per_page", 50)), 200)
        action    = request.args.get("action")
        resource  = request.args.get("resource")
        date_from = request.args.get("date_from")
        date_to   = request.args.get("date_to")
        filter_uid = request.args.get("user_id")

        q = AuditLog.query
        if not is_admin:
            q = q.filter_by(user_id=uid)
        elif filter_uid:
            q = q.filter_by(user_id=int(filter_uid))

        if action:   q = q.filter(AuditLog.action.ilike(f"%{action}%"))
        if resource: q = q.filter(AuditLog.resource.ilike(f"%{resource}%"))
        if date_from:
            from datetime import datetime
            q = q.filter(AuditLog.timestamp >= datetime.fromisoformat(date_from))
        if date_to:
            from datetime import datetime
            q = q.filter(AuditLog.timestamp <= datetime.fromisoformat(date_to))

        paginated = q.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=per_page, error_out=False)
        return {
            "logs": [l.to_dict() for l in paginated.items],
            "total": paginated.total,
            "page": paginated.page,
            "pages": paginated.pages,
            "per_page": per_page,
        }

@audit_ns.route("/logs/me/")
class MyLogs(Resource):
    @audit_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        uid = int(get_jwt_identity())
        page     = int(request.args.get("page", 1))
        per_page = min(int(request.args.get("per_page", 50)), 200)
        q = AuditLog.query.filter_by(user_id=uid).order_by(AuditLog.timestamp.desc())
        paginated = q.paginate(page=page, per_page=per_page, error_out=False)
        return {
            "logs": [l.to_dict() for l in paginated.items],
            "total": paginated.total,
            "page": paginated.page,
            "pages": paginated.pages,
            "per_page": per_page,
        }
