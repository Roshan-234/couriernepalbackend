import logging
import os
import uuid
from flask import current_app, request, url_for, g, make_response
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import (
    create_access_token, create_refresh_token, jwt_required, get_jwt_identity,
    set_access_cookies, set_refresh_cookies, unset_jwt_cookies, decode_token,
)
from marshmallow import ValidationError
from app.extensions import db, limiter, mail
from app.models.user import User, UserStatus
from app.models.role import Role
from .utils import validate_password_strength, require_roles
from .schemas import PasswordResetRequestSchema, PasswordResetConfirmSchema
from flask_mail import Message
from datetime import datetime

auth_ns = Namespace("auth", description="Authentication")

register_model = auth_ns.model("Register", {
    "email":      fields.String(required=True, example="john@example.com"),
    "password":   fields.String(required=True, example="P@ssw0rd!"),
    "first_name": fields.String(required=True),
    "last_name":  fields.String(required=True)
})

login_model = auth_ns.model("Login", {
    "email":    fields.String(required=True, example="john@example.com"),
    "password": fields.String(required=True, example="P@ssw0rd!")
})

reset_req_model = auth_ns.model("PasswordResetRequest", {
    "email": fields.String(required=True, example="you@example.com")
})
reset_confirm_model = auth_ns.model("PasswordResetConfirm", {
    "token":    fields.String(required=True, description="Reset token"),
    "password": fields.String(required=True, description="New password (min 8 chars)")
})


token_model = auth_ns.model("TokenResponse", {
    "access_token":  fields.String(),
    "refresh_token": fields.String(),
    "user":          fields.Nested(auth_ns.model("User", {
        "id":   fields.Integer(),
        "email":fields.String(),
        "first_name": fields.String(),
        "last_name":  fields.String(),
        "status":     fields.String(),
        "roles":      fields.List(fields.String())
    }))
})

#: A bcrypt hash to compare against when the email is unknown. Without it an
#: unknown address returns measurably faster than a known one, which is a
#: free account-enumeration oracle. Built once, lazily.
_DECOY_HASH = None


def _decoy_hash():
    global _DECOY_HASH
    if _DECOY_HASH is None:
        from app.extensions import bcrypt
        _DECOY_HASH = bcrypt.generate_password_hash("decoy-for-timing").decode("utf-8")
    return _DECOY_HASH


def _burn_password_time(password):
    """Spend the same work on an unknown account as on a real one."""
    from app.extensions import bcrypt
    try:
        bcrypt.check_password_hash(_decoy_hash(), password or "")
    except (ValueError, TypeError):
        pass


def _track_session(user_id, access_token, refresh_token):
    """Persist a revocable UserSession row for a freshly issued access token."""
    from app.models.user_session import UserSession
    from app.net import client_ip
    claims = decode_token(refresh_token)
    session = UserSession(
        user_id=user_id,
        jti=decode_token(access_token)["sid"],
        refresh_jti=claims.get("jti"),
        ip_address=client_ip(),
        user_agent=(request.user_agent.string or "")[:500] if request.user_agent else "",
        expires_at=datetime.utcfromtimestamp(claims["exp"]) if claims.get("exp") else None,
    )
    db.session.add(session)
    db.session.commit()
    return session


def _auth_body(payload, access=None, refresh=None):
    """Attach tokens to the body only where the deployment wants them there."""
    if current_app.config.get("AUTH_TOKENS_IN_BODY", True):
        if access is not None:
            payload["access_token"] = access
        if refresh is not None:
            payload["refresh_token"] = refresh
    return payload


@auth_ns.route("/register")
class Register(Resource):
    @auth_ns.expect(register_model, validate=True)
    @limiter.limit("5/minute")
    def post(self):
        data = request.get_json()
        for key in ('email', 'first_name', 'last_name'):
            data[key] = data[key].strip()
            if not data[key]:
                return {"msg": f"{key.replace('_', ' ').title()} is required"}, 400
        if any(len(data[key]) > 50 for key in ('first_name', 'last_name')):
            return {"msg": "Names must contain at most 50 characters"}, 400
        data['email'] = data['email'].lower()
        if '@' not in data['email'] or len(data['email']) > 120:
            return {"msg": "Enter a valid email address"}, 400
        # password strength
        errs = validate_password_strength(data["password"])
        if errs:
            return {"msg": "Weak password", "errors": errs}, 400

        # email uniqueness
        if User.query.filter_by(email=data["email"]).first():
            return {"msg": "Email already registered"}, 409

        # username from email
        base = data["email"].split("@")[0][:70]
        uname = base
        i = 1
        while User.query.filter_by(username=uname).first():
            uname = f"{base}{i}"; i += 1

        u = User(
            username=uname,
            email=data["email"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            status=UserStatus.ACTIVE
        )
        u.set_password(data["password"])

        # Public signup must never bootstrap privileged accounts.
        u.roles.append(Role.query.filter_by(name="customer").one())

        db.session.add(u)
        db.session.commit()
        return {"msg": "Registered", "user": u.to_dict()}, 201

@auth_ns.route("/login")
class Login(Resource):
    @auth_ns.expect(login_model, validate=True)
    @auth_ns.doc(model=token_model)
    @limiter.limit("10/minute")
    def post(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {"msg": "A JSON object is required"}, 400
        email = data.get("email")
        password = data.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            return {"msg": "Email and password are required"}, 400

        user = User.query.filter_by(email=email.strip().lower()).first()

        # One message and one amount of work for every failure, so neither the
        # wording nor the timing says whether the address exists.
        if not user:
            _burn_password_time(password)
            auth_ns.abort(401, "Bad credentials")

        if user.is_locked:
            # Deliberately the same 401 as a wrong password: telling an
            # attacker they have locked an account confirms it exists.
            _burn_password_time(password)
            auth_ns.abort(401, "Bad credentials")

        if not user.check_password(password):
            locked = user.record_failed_login()
            db.session.commit()
            if locked:
                current_app.logger.warning(
                    "Account %s locked after repeated failed sign-ins", user.id
                )
            auth_ns.abort(401, "Bad credentials")

        if user.status != UserStatus.ACTIVE:
            auth_ns.abort(403, "Account not active")

        user.record_successful_login()
        db.session.commit()

        roles = [r.name for r in user.roles]
        sid = uuid.uuid4().hex
        access = create_access_token(identity=str(user.id), additional_claims={"roles": roles, "sid": sid})
        refresh = create_refresh_token(identity=str(user.id), additional_claims={"sid": sid})
        _track_session(user.id, access, refresh)

        # Let the audit middleware capture this user's id (no JWT on the login request itself)
        g.audit_user_id = user.id

        # Cookies are what the browser uses. Whether the tokens also appear in
        # the body is a deployment choice: see AUTH_TOKENS_IN_BODY.
        body = _auth_body(
            {"user": user.to_dict(), "must_change_password": user.must_change_password},
            access, refresh,
        )
        resp = make_response(body, 200)
        set_access_cookies(resp, access)
        set_refresh_cookies(resp, refresh)
        return resp

@auth_ns.route("/refresh")
class Refresh(Resource):
    @jwt_required(refresh=True)
    def post(self):
        """Exchange a refresh token for a new pair, rotating the refresh token.

        Rotation is what makes a stolen refresh cookie detectable. Each one is
        valid exactly once: presenting a superseded token means two parties
        hold it, so the session is revoked rather than renewed and the real
        user is signed out — visibly — instead of sharing their account.
        """
        from flask_jwt_extended import get_jwt
        from app.models.user_session import UserSession

        claims = get_jwt()
        uid = get_jwt_identity()
        user = db.session.get(User, int(uid))
        if not user or user.status != UserStatus.ACTIVE:
            return {"msg": "Account not active"}, 401

        session = UserSession.query.filter_by(jti=claims.get("sid"), user_id=user.id).first()
        if not session or session.is_revoked:
            return {"msg": "Session is no longer valid"}, 401

        presented = claims.get("jti")
        if session.refresh_jti and presented != session.refresh_jti:
            # This token was already exchanged. Either it was replayed by
            # someone who copied it, or the legitimate client raced itself;
            # both are answered by ending the session.
            session.revoke(reason="refresh_reuse")
            db.session.commit()
            current_app.logger.warning(
                "Refresh token reuse on session %s for user %s", session.id, user.id
            )
            resp = make_response({"msg": "Session ended for your security. Please sign in again."}, 401)
            unset_jwt_cookies(resp)
            return resp

        roles = [r.name for r in user.roles]
        sid = claims["sid"]
        access = create_access_token(identity=str(uid), additional_claims={"roles": roles, "sid": sid})
        refresh = create_refresh_token(identity=str(uid), additional_claims={"sid": sid})

        session.refresh_jti = decode_token(refresh).get("jti")
        session.rotated_at = datetime.utcnow()
        session.last_active = datetime.utcnow()
        db.session.commit()

        resp = make_response(_auth_body({"msg": "Refreshed"}, access, refresh), 200)
        set_access_cookies(resp, access)
        set_refresh_cookies(resp, refresh)
        return resp

@auth_ns.route("/profile")
class Profile(Resource):
    @jwt_required()
    def get(self):
        uid = int(get_jwt_identity())
        return User.query.get_or_404(uid).to_dict()

    @jwt_required()
    def patch(self):
        """Update the signed-in user's name without exposing role changes."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {"msg": "A JSON object is required"}, 400
        user = db.session.get(User, int(get_jwt_identity()))
        if not user:
            return {"msg": "User not found"}, 404
        for key in ('first_name', 'last_name'):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 50:
                return {"msg": "First and last name must contain 1–50 characters"}, 400
        user.first_name = data['first_name'].strip()
        user.last_name = data['last_name'].strip()
        db.session.commit()
        return user.to_dict()


@auth_ns.route("/password-reset")
class PasswordResetRequest(Resource):
    @auth_ns.expect(reset_req_model)
    @limiter.limit("3/hour")
    def post(self):
        """Send password-reset link to email."""
        data = request.get_json()
        try:
            valid = PasswordResetRequestSchema().load(data)
        except ValidationError as err:
            return {"msg": "Validation error", "errors": err.messages}, 400

        user = User.query.filter_by(email=valid["email"].strip().lower()).first()
        if user:
            try:
                token = user.generate_reset_token()
                frontend_url = current_app.config["FRONTEND_URL"]
                reset_url = f"{frontend_url}/auth/reset-password?token={token}"

                msg = Message(
                    subject="Moonlight Freight Password Reset",
                    recipients=[user.email],
                    body=(
                        f"Hello {user.first_name},\n\n"
                        f"Reset your password using this link:\n\n"
                        f"{reset_url}\n\n"
                        f"This link expires in 1 hour. If you didn't request this, ignore it."
                    )
                )
                mail.send(msg)
                # The address is not logged: these logs are shipped off the
                # host and a reset request is a fact about a named customer.
                current_app.logger.info("Password reset email sent for user %s", user.id)
            except Exception:
                # Swallowed deliberately. Returning 500 only for addresses that
                # exist, while unknown ones return 200, tells an attacker which
                # is which. The failure is logged for operators instead.
                logging.exception("Failed to send reset email for user %s", user.id)

        # One answer, whether or not the address is on file.
        return {"msg": "If that address is registered, a reset link is on its way."}, 200

@auth_ns.route("/password-reset/confirm")
class PasswordResetConfirm(Resource):
    @auth_ns.expect(reset_confirm_model)
    @limiter.limit("10/hour")
    def post(self):
        """Verify token and reset password."""
        data = request.get_json()
        try:
            valid = PasswordResetConfirmSchema().load(data)
        except ValidationError as err:
            return {"msg": "Validation error", "errors": err.messages}, 400

        # Looked up by digest: the clear token is never stored, so a database
        # reader cannot take over an account with a reset in flight.
        user = User.by_reset_token(valid["token"])
        if not user:
            # Expired and unknown answer alike, so the response says nothing
            # about whether a token ever existed.
            return {"msg": "That reset link is invalid or has expired."}, 400

        errors = validate_password_strength(valid["password"])
        if errors:
            return {"msg": "Weak password", "errors": errors}, 400

        from app.models.user_session import UserSession
        UserSession.query.filter_by(user_id=user.id, is_revoked=False).update(
            {"is_revoked": True, "revoked_reason": "password_reset"}
        )
        user.set_password(valid["password"])
        user.clear_reset_token()
        # A reset is also the way out of a lockout.
        user.failed_login_count = 0
        user.locked_until = None
        db.session.commit()

        return {"msg": "Your password has been reset successfully."}, 200


@auth_ns.route("/logout")
class Logout(Resource):
    @auth_ns.doc(security="Bearer")
    @jwt_required()
    def post(self):
        from flask_jwt_extended import get_jwt
        from app.models.user_session import UserSession
        jti = get_jwt().get("sid")
        if jti:
            sess = UserSession.query.filter_by(jti=jti).first()
            if sess:
                sess.revoke(reason="logout")
                db.session.commit()
        resp = make_response({"msg": "Logged out successfully"}, 200)
        unset_jwt_cookies(resp)
        return resp


@auth_ns.route("/password")
class ChangePassword(Resource):
    @auth_ns.doc(security="Bearer")
    @jwt_required()
    @limiter.limit("10/hour")
    def post(self):
        """Change your own password.

        Until now the only route to a new password was the reset email, which
        meant a user who simply wanted to rotate theirs had to pretend to have
        forgotten it. Changing it signs out every other session, because the
        usual reason to change a password is that someone else may have it.
        """
        from flask_jwt_extended import get_jwt
        from app.models.user_session import UserSession

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {"msg": "A JSON object is required"}, 400
        current = data.get("current_password")
        replacement = data.get("new_password")
        if not isinstance(current, str) or not isinstance(replacement, str):
            return {"msg": "Current and new password are required"}, 400

        user = db.session.get(User, int(get_jwt_identity()))
        if not user:
            return {"msg": "User not found"}, 404
        if not user.check_password(current):
            return {"msg": "Your current password is not correct"}, 400
        if current == replacement:
            return {"msg": "Choose a password you have not used here before"}, 400

        errors = validate_password_strength(replacement)
        if errors:
            return {"msg": "Weak password", "errors": errors}, 400

        keep = get_jwt().get("sid")
        revoked = UserSession.query.filter(
            UserSession.user_id == user.id,
            UserSession.is_revoked.is_(False),
            UserSession.jti != keep,
        ).update({"is_revoked": True, "revoked_reason": "password_change"},
                 synchronize_session=False)
        user.set_password(replacement)
        db.session.commit()
        return {"msg": "Password changed", "other_sessions_signed_out": int(revoked or 0)}, 200
