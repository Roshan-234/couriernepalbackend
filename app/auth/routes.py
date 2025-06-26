from flask import request, url_for
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
        """Request a password-reset email."""
        data = request.get_json()
        try:
            valid = PasswordResetRequestSchema().load(data)
        except ValidationError as err:
            return {"msg": "Validation error", "errors": err.messages}, 400

        user = User.query.filter_by(email=valid["email"]).first()
        if user:
            token = user.generate_reset_token()
            # Assuming your Next.js reset page is at /reset-password
            reset_url = url_for("reset-password", token=token, _external=True)
            msg = Message(
                subject="Moonlight Freight Password Reset",
                recipients=[user.email]
            )
            msg.body = (
                f"Hello {user.first_name},\n\n"
                f"Please reset your password using the link below:\n\n"
                f"{reset_url}\n\n"
                f"If you did not request this, please ignore."
            )
            mail.send(msg)

        # Always 200 to prevent email enumeration
        return {"msg": "If your email exists, you’ll receive a reset link shortly."}, 200

@auth_ns.route("/password-reset/confirm")
class PasswordResetConfirm(Resource):
    @auth_ns.expect(reset_confirm_model)
    def post(self):
        """Confirm reset token and set new password."""
        data = request.get_json()
        try:
            valid = PasswordResetConfirmSchema().load(data)
        except ValidationError as err:
            return {"msg": "Validation error", "errors": err.messages}, 400

        user = User.query.filter_by(password_reset_token=valid["token"]).first()
        if (
            not user or
            not user.password_reset_expires or
            user.password_reset_expires < datetime.utcnow()
        ):
            return {"msg": "Invalid or expired token"}, 400

        user.set_password(valid["password"])
        user.password_reset_token   = None
        user.password_reset_expires = None
        db.session.commit()

        return {"msg": "Your password has been reset successfully."}, 200