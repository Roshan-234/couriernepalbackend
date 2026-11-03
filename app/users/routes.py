from flask import request
from app.validation import json_object, text
from werkzeug.exceptions import BadRequest
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
import secrets

from app.extensions import db
from app.models.user import User, UserStatus
from app.models.role import Role
from app.auth.utils import require_roles

user_ns = Namespace("users", description="User management")

role_field = fields.String(description="Role name")
user_out = user_ns.model("UserOut", {
    "id":         fields.Integer(),
    "username":   fields.String(),
    "email":      fields.String(),
    "first_name": fields.String(),
    "last_name":  fields.String(),
    "status":     fields.String(),
    "roles":      fields.List(role_field),
    "must_change_password": fields.Boolean(),
    "last_login_at": fields.String(),
})

user_in = user_ns.model("UserIn", {
    "email":      fields.String(required=True),
    "first_name": fields.String(),
    "last_name":  fields.String(),
    "status":     fields.String(enum=[s.value for s in UserStatus]),
    "roles":      fields.List(role_field)
})

@user_ns.route("/")
class UserList(Resource):
    @user_ns.doc(security="Bearer")
    @user_ns.marshal_list_with(user_out)
    @require_roles("admin", "super_admin")
    def get(self):
        return User.query.all()

    @user_ns.doc(security="Bearer")
    @user_ns.expect(user_in)
    # Deliberately unmarshalled: user_out does not declare
    # `temporary_password`, so marshalling silently dropped the one piece of
    # this response the administrator actually needs, leaving the new account
    # unreachable.
    @require_roles("super_admin")
    def post(self):
        data = request.get_json()
        if User.query.filter_by(email=data["email"]).first():
            user_ns.abort(409, "Email exists")

        # username + temp password
        base = data["email"].split("@")[0]
        uname = base; i = 1
        while User.query.filter_by(username=uname).first():
            uname = f"{base}{i}"; i += 1
        temp_pw = secrets.token_urlsafe(8)

        u = User(
            username=uname,
            email=data["email"],
            first_name=data.get("first_name",""),
            last_name=data.get("last_name",""),
            status=UserStatus(data.get("status", UserStatus.ACTIVE.value))
        )
        u.set_password(temp_pw)
        # set_password clears this, so flag it afterwards: a password an
        # administrator chose and read is not a secret only the user knows.
        u.must_change_password = True
        u.password_reset_expires = None

        # assign roles or default customer
        if data.get("roles"):
            for rn in data["roles"]:
                r = Role.query.filter_by(name=rn).first()
                if r: u.roles.append(r)
        else:
            u.roles.append(Role.query.filter_by(name="customer").first())

        db.session.add(u); db.session.commit()
        out = u.to_dict()
        out["temporary_password"] = temp_pw
        out["notice"] = (
            "Give this to the account holder over a channel they already "
            "trust, and have them change it at first sign-in. It is shown "
            "once and is not stored in clear."
        )
        return out, 201

@user_ns.route("/<int:id>")
class UserDetail(Resource):
    @user_ns.doc(security="Bearer")
    @user_ns.marshal_with(user_out)
    @jwt_required()
    def get(self, id):
        me = User.query.get(int(get_jwt_identity()))
        if me.id != id and not any(r.name in ("admin","super_admin") for r in me.roles):
            user_ns.abort(403, "Forbidden")
        return User.query.get_or_404(id)

    @user_ns.doc(security="Bearer")
    @user_ns.expect(user_in)
    @user_ns.marshal_with(user_out)
    @require_roles("admin", "super_admin")
    def put(self, id):
        u = User.query.get_or_404(id)
        data = json_object()
        caller = db.session.get(User, int(get_jwt_identity()))
        super_admin = any(role.name == 'super_admin' for role in caller.roles)
        if any(role.name == 'super_admin' for role in u.roles) and not super_admin:
            user_ns.abort(403, 'Only super_admin may edit administrator accounts')
        # An admin could otherwise set a peer admin's email to one they
        # control, trigger a password reset and own the account. Changing
        # another administrator's address is a super_admin action.
        if ('email' in data and u.id != caller.id and u.is_admin and not super_admin):
            user_ns.abort(403, "Only super_admin may change another administrator's email")
        if set(data) - {'email', 'first_name', 'last_name', 'status', 'roles'}:
            user_ns.abort(400, 'Unknown user field')
        if 'roles' in data:
            if not super_admin:
                user_ns.abort(403, 'Only super_admin may update roles')
            if not isinstance(data['roles'], list) or not data['roles'] or not all(isinstance(r, str) for r in data['roles']):
                user_ns.abort(400, 'Provide a non-empty list of roles')
            known = {r.name for r in Role.query.all()}
            if set(data['roles']) - known:
                user_ns.abort(400, 'Unknown role')
        if 'status' in data and data['status'] not in [s.value for s in UserStatus]:
            user_ns.abort(400, 'Invalid account status')
        if u.id == caller.id and (data.get('status', 'active') != 'active' or ('roles' in data and set(data['roles']) != {r.name for r in u.roles})):
            user_ns.abort(400, 'You cannot disable or change your own roles')
        for key in ('first_name', 'last_name'):
            if key in data:
                data[key] = text(data[key], key, 50, True)
        if 'email' in data:
            data['email'] = text(data['email'], 'email', 120, True).lower()
            if '@' not in data['email']:
                user_ns.abort(400, 'Invalid email address')
            if User.query.filter(User.email == data['email'], User.id != id).first():
                user_ns.abort(409, 'Email already registered')
        # Anything that changes who the account is, or what it may do, has to
        # invalidate tokens already issued for it: an access token carries the
        # old roles until it expires, and a stolen session should not survive
        # the administrator disabling the account.
        email_changed = 'email' in data and data['email'] != u.email
        status_changed = 'status' in data and data['status'] != u.status.value
        roles_changed = 'roles' in data and set(data['roles']) != {r.name for r in u.roles}

        u.email      = data.get("email", u.email)
        u.first_name = data.get("first_name", u.first_name)
        u.last_name  = data.get("last_name", u.last_name)
        u.status     = UserStatus(data.get("status", u.status.value))

        # only super_admin can change roles
        if "roles" in data:
            caller = User.query.get(get_jwt_identity())
            if any(r.name=="super_admin" for r in caller.roles):
                u.roles = []
                for rn in data["roles"]:
                    r = Role.query.filter_by(name=rn).first()
                    if r: u.roles.append(r)
            else:
                user_ns.abort(403, "Only super_admin may update roles")

        if email_changed or status_changed or roles_changed:
            from app.models.user_session import UserSession
            reason = ('email_changed' if email_changed
                      else 'roles_changed' if roles_changed else 'status_changed')
            UserSession.query.filter_by(user_id=u.id, is_revoked=False).update(
                {"is_revoked": True, "revoked_reason": reason}, synchronize_session=False
            )
            # A pending reset would still be valid against the old address.
            if email_changed:
                u.clear_reset_token()

        db.session.commit()
        return u

    @user_ns.doc(security="Bearer")
    @require_roles("super_admin")
    def delete(self, id):
        u = User.query.get_or_404(id)

        # Prevent admins from deleting their own account
        if u.id == int(get_jwt_identity()):
            user_ns.abort(400, "You cannot delete your own account")

        # Refuse to orphan/destroy business records. Sessions, notifications
        # (cascade) and audit logs (nullable) are cleaned up automatically.
        from app.models.shipment import Shipment
        from app.models.invoice import Invoice
        from app.models.packing_list import PackingList
        blockers = []
        if Shipment.query.filter_by(user_id=u.id).count():
            blockers.append("shipments")
        if Invoice.query.filter_by(created_by=u.id).count():
            blockers.append("invoices")
        if PackingList.query.filter_by(created_by=u.id).count():
            blockers.append("packing lists")
        if u.agent_profile is not None:
            blockers.append("agent profile")
        if blockers:
            user_ns.abort(
                409,
                "Cannot delete user with existing "
                + ", ".join(blockers)
                + ". Deactivate the account instead.",
            )

        db.session.delete(u)
        db.session.commit()
        return {"msg": "Deleted"}, 204
