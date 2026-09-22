from flask import request
from flask_restx import Namespace, Resource
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from app.extensions import db
from app.models.user_session import UserSession
from app.models.user import User
from app.auth.utils import require_roles

sessions_ns = Namespace("sessions", description="Session management")

@sessions_ns.route("/")
class SessionList(Resource):
    @sessions_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        uid = int(get_jwt_identity())
        user = User.query.get(uid)
        roles = [r.name for r in user.roles]
        if "super_admin" in roles:
            sessions = UserSession.query.filter_by(is_revoked=False).order_by(UserSession.created_at.desc()).all()
        else:
            sessions = UserSession.query.filter_by(user_id=uid, is_revoked=False).all()
        return [s.to_dict() for s in sessions]

@sessions_ns.route("/<int:session_id>")
class SessionDetail(Resource):
    @sessions_ns.doc(security="Bearer")
    @jwt_required()
    def delete(self, session_id):
        uid = int(get_jwt_identity())
        sess = UserSession.query.get_or_404(session_id)
        user = User.query.get(uid)
        roles = [r.name for r in user.roles]
        if sess.user_id != uid and "super_admin" not in roles:
            return {"msg": "Forbidden"}, 403
        sess.is_revoked = True
        db.session.commit()
        return {"msg": "Session revoked"}

@sessions_ns.route("/all")
class RevokeAll(Resource):
    @sessions_ns.doc(security="Bearer")
    @jwt_required()
    def delete(self):
        uid = int(get_jwt_identity())
        UserSession.query.filter_by(user_id=uid, is_revoked=False).update({"is_revoked": True})
        db.session.commit()
        return {"msg": "All sessions revoked"}
