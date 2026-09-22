from flask import request
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
    "roles":      fields.List(role_field)
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
    @user_ns.marshal_with(user_out, code=201)
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
        data = request.get_json()
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
