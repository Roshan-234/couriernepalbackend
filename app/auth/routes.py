import logging
import os
from flask import current_app, request, url_for
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
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

@auth_ns.route("/register")
class Register(Resource):
    @auth_ns.expect(register_model)
    @limiter.limit("5/minute")
    def post(self):
        data = request.get_json()
        # password strength
        errs = validate_password_strength(data["password"])
        if errs:
            return {"msg": "Weak password", "errors": errs}, 400

        # email uniqueness
        if User.query.filter_by(email=data["email"]).first():
            return {"msg": "Email already registered"}, 409

        # username from email
        base = data["email"].split("@")[0]
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

        # first user → super_admin, else customer
        if User.query.count() == 0:
            u.roles.append(Role.query.filter_by(name="super_admin").first())
        else:
            u.roles.append(Role.query.filter_by(name="customer").first())

        db.session.add(u)
        db.session.commit()
        return {"msg": "Registered", "user": u.to_dict()}, 201

@auth_ns.route("/login")
class Login(Resource):
    @auth_ns.expect(login_model)
    @auth_ns.marshal_with(token_model)
    @limiter.limit("10/minute")
    def post(self):
        data = request.get_json()
        user = User.query.filter_by(email=data["email"]).first()
        if not user or not user.check_password(data["password"]):
            auth_ns.abort(401, "Bad credentials")
        if user.status != UserStatus.ACTIVE:
            auth_ns.abort(403, "Account not active")

        roles = [r.name for r in user.roles]
        access  = create_access_token(identity=user.id, additional_claims={"roles": roles})
        refresh = create_refresh_token(identity=user.id)
        return {"access_token": access, "refresh_token": refresh, "user": user.to_dict()}

@auth_ns.route("/refresh")
class Refresh(Resource):
    @jwt_required(refresh=True)
    def post(self):
        uid = get_jwt_identity()
        token = create_access_token(identity=uid)
        return {"access_token": token}

@auth_ns.route("/profile")
class Profile(Resource):
    @jwt_required()
    def get(self):
        uid = get_jwt_identity()
        return User.query.get_or_404(uid).to_dict()


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

        user = User.query.filter_by(email=valid["email"]).first()
        if user:
            try:
                token = user.generate_reset_token()
                # Use environment or fallback
                frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
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
                current_app.logger.info(f"Password reset email sent to {user.email}")
            except Exception as e:
                logging.exception("Failed to send reset email")
                return {"msg": "Internal error while sending email."}, 500

        return {"msg": "Reset link will be sent to your mail address."}, 200

@auth_ns.route("/password-reset/confirm")
class PasswordResetConfirm(Resource):
    @auth_ns.expect(reset_confirm_model)
    def post(self):
        """Verify token and reset password."""
        data = request.get_json()
        try:
            valid = PasswordResetConfirmSchema().load(data)
        except ValidationError as err:
            return {"msg": "Validation error", "errors": err.messages}, 400

        user = User.query.filter_by(password_reset_token=valid["token"]).first()

        if not user:
            return {"msg": "Invalid token"}, 400

        if not user.password_reset_expires or user.password_reset_expires < datetime.utcnow():
            return {"msg": "Token has expired"}, 400

        user.set_password(valid["password"])
        user.password_reset_token = None
        user.password_reset_expires = None
        db.session.commit()

        return {"msg": "Your password has been reset successfully."}, 200