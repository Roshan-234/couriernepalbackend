"""
Packing List CRUD + Excel export.

Endpoints:
  GET    /api/packing-lists/           List (admin: all, user: own)
  POST   /api/packing-lists/           Create
  GET    /api/packing-lists/<id>       Detail
  PUT    /api/packing-lists/<id>       Update (while draft)
  DELETE /api/packing-lists/<id>       Delete (while draft)
  POST   /api/packing-lists/<id>/generate   Generate / re-generate Excel
  GET    /api/packing-lists/<id>/download   Download the Excel file
  GET    /api/packing-lists/search?q=       Search by sender / receiver / pl_number
"""
import os
from flask import request, send_file, current_app
from flask_restx import Namespace, Resource
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.extensions import db
from app.validation import document_data, json_object, pagination
from werkzeug.exceptions import BadRequest
from app.models.packing_list import PackingList
from app.models.user import User

pl_ns = Namespace("packing-lists", description="Packing Lists")


def _carton_count(items):
    """How many cartons a list describes.

    The number of distinct box labels, or one carton when nothing is
    labelled -- an unlabelled list is still a shipment of something.
    """
    labels = {(item.get("box") or "").strip() for item in items or []}
    labelled = {label for label in labels if label}
    return len(labelled) or (1 if items else 0)


def _storage_dir():
    return os.path.join(current_app.root_path, "..", "storage")


def _is_admin(user):
    return any(r.name in ("super_admin", "admin") for r in user.roles)


def _own_or_admin(pl, user):
    return pl.created_by == user.id or _is_admin(user)


@pl_ns.route("/")
class PackingListList(Resource):
    @pl_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        page, per_page = pagination()
        status   = request.args.get("status")
        q        = PackingList.query if _is_admin(user) else PackingList.query.filter_by(created_by=uid)
        if status:
            q = q.filter_by(status=status)
        pag = q.order_by(PackingList.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        return {
            "items":    [p.to_dict() for p in pag.items],
            "total":    pag.total,
            "page":     pag.page,
            "pages":    pag.pages,
            "per_page": per_page,
        }

    @pl_ns.doc(security="Bearer")
    @jwt_required()
    def post(self):
        uid  = int(get_jwt_identity())
        data = document_data(json_object(), uid, "packing")

        items = data.get("items", [])
        total_wt  = sum(float(i.get("total_weight_kg") or (i.get("quantity",0)*i.get("unit_weight_kg",0))) for i in items)
        total_qty = sum(int(i.get("quantity", 0)) for i in items)

        pl = PackingList(
            pl_number       = PackingList.generate_number(),
            shipment_id     = data.get("shipment_id"),
            created_by      = uid,
            sender_name     = data.get("sender_name", "").strip(),
            sender_address  = data.get("sender_address", ""),
            sender_phone    = data.get("sender_phone", ""),
            sender_country  = data.get("sender_country", "Nepal"),
            sender_email    = data.get("sender_email", ""),
            invoice_no      = data.get("invoice_no", ""),
            mode_of_shipment= data.get("mode_of_shipment", "BY-AIR"),
            goods_summary   = data.get("goods_summary", ""),
            quantity_unit   = data.get("quantity_unit", "PCS"),
            signed_for      = data.get("signed_for", ""),
            receiver_name   = data.get("receiver_name", "").strip(),
            receiver_address= data.get("receiver_address", ""),
            receiver_phone  = data.get("receiver_phone", ""),
            receiver_country= data.get("receiver_country", ""),
            items           = items,
            # Cartons, not line items. Defaulting to len(items) made a
            # twelve-line list read as twelve boxes on the paperwork.
            total_packages  = data.get("total_packages", _carton_count(items)),
            total_weight_kg = round(total_wt, 3),
            total_items     = total_qty,
            notes           = data.get("notes", ""),
            status          = "draft",
        )
        db.session.add(pl)
        db.session.commit()
        return pl.to_dict(), 201


@pl_ns.route("/search")
class PackingListSearch(Resource):
    @pl_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        q_str = request.args.get("q", "").strip()
        base  = PackingList.query if _is_admin(user) else PackingList.query.filter_by(created_by=uid)
        if q_str:
            like = f"%{q_str}%"
            base = base.filter(
                db.or_(
                    PackingList.sender_name.ilike(like),
                    PackingList.receiver_name.ilike(like),
                    PackingList.pl_number.ilike(like),
                )
            )
        results = base.order_by(PackingList.created_at.desc()).limit(50).all()
        return [p.to_dict() for p in results]


@pl_ns.route("/<int:id>")
class PackingListDetail(Resource):
    @pl_ns.doc(security="Bearer")
    @jwt_required()
    def get(self, id):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        pl   = PackingList.query.get_or_404(id)
        if not _own_or_admin(pl, user):
            pl_ns.abort(403, "Access denied")
        return pl.to_dict()

    @pl_ns.doc(security="Bearer")
    @jwt_required()
    def put(self, id):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        pl   = PackingList.query.get_or_404(id)
        if not _own_or_admin(pl, user):
            pl_ns.abort(403, "Access denied")
        if pl.status == "finalized" and not _is_admin(user):
            pl_ns.abort(400, "Finalized packing lists cannot be edited")

        data = document_data(json_object(), uid, "packing")
        updatable = [
            "sender_name","sender_address","sender_phone","sender_country",
            "receiver_name","receiver_address","receiver_phone","receiver_country",
            "items","notes","total_packages","status","shipment_id",
            "sender_email","invoice_no","mode_of_shipment","goods_summary",
            "quantity_unit","signed_for",
        ]
        for field in updatable:
            if field in data:
                setattr(pl, field, data[field])

        items = pl.items or []
        pl.total_weight_kg = round(sum(
            float(i.get("total_weight_kg") or (i.get("quantity",0)*i.get("unit_weight_kg",0)))
            for i in items), 3)
        pl.total_items = sum(int(i.get("quantity", 0)) for i in items)

        db.session.commit()
        return pl.to_dict()

    @pl_ns.doc(security="Bearer")
    @jwt_required()
    def delete(self, id):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        pl   = PackingList.query.get_or_404(id)
        if not _own_or_admin(pl, user):
            pl_ns.abort(403, "Access denied")
        if pl.status == "finalized" and not _is_admin(user):
            pl_ns.abort(400, "Cannot delete a finalized packing list")
        db.session.delete(pl)
        db.session.commit()
        return {"msg": "Deleted"}


@pl_ns.route("/<int:id>/generate")
class PackingListGenerate(Resource):
    @pl_ns.doc(security="Bearer")
    @jwt_required()
    def post(self, id):
        """Generate (or re-generate) the Excel file for this packing list."""
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        pl   = PackingList.query.get_or_404(id)
        if not _own_or_admin(pl, user):
            pl_ns.abort(403, "Access denied")

        from app.utils.house_format import generate_packing_list_excel
        storage = _storage_dir()
        fpath, fname = generate_packing_list_excel(pl, storage)
        pl.file_path = fpath
        pl.file_name = fname
        db.session.commit()
        return {"msg": "Excel generated", "file_name": fname}


@pl_ns.route("/<int:id>/download")
class PackingListDownload(Resource):
    @pl_ns.doc(security="Bearer")
    @jwt_required()
    def get(self, id):
        """Stream the Excel file to the browser."""
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        pl   = PackingList.query.get_or_404(id)
        if not _own_or_admin(pl, user):
            pl_ns.abort(403, "Access denied")
        if not pl.file_path or not os.path.exists(pl.file_path):
            pl_ns.abort(404, "Excel not generated yet. POST to /generate first.")
        return send_file(
            pl.file_path,
            as_attachment=True,
            download_name=pl.file_name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
