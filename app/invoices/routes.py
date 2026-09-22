"""
Commercial Invoice CRUD + Excel export.

Endpoints:
  GET    /api/invoices/            List (admin: all, user: own)
  POST   /api/invoices/            Create
  GET    /api/invoices/<id>        Detail
  PUT    /api/invoices/<id>        Update (while draft)
  DELETE /api/invoices/<id>        Delete (while draft)
  POST   /api/invoices/<id>/generate    Generate / re-generate Excel
  GET    /api/invoices/<id>/download    Download the Excel file
  GET    /api/invoices/search?q=        Search by party name / invoice number
"""
import os
from datetime import datetime, timedelta
from flask import request, send_file, current_app
from flask_restx import Namespace, Resource
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.extensions import db
from app.models.invoice import Invoice
from app.models.user import User

inv_ns = Namespace("invoices", description="Commercial Invoices")


def _storage_dir():
    return os.path.join(current_app.root_path, "..", "storage")


def _is_admin(user):
    return any(r.name in ("super_admin", "admin") for r in user.roles)


def _own_or_admin(inv, user):
    return inv.created_by == user.id or _is_admin(user)


def _compute_totals(data: dict) -> dict:
    items    = data.get("items", [])
    subtotal = sum(
        float(i.get("total_price") or (i.get("quantity", 0) * i.get("unit_price", 0)))
        for i in items
    )
    discount    = float(data.get("discount", 0))
    tax_pct     = float(data.get("tax_percentage", 0))
    shipping    = float(data.get("shipping_charge", 0))
    taxable     = subtotal - discount
    tax_amount  = round(taxable * tax_pct / 100, 2)
    total       = round(taxable + tax_amount + shipping, 2)
    return {
        "subtotal":      round(subtotal, 2),
        "discount":      discount,
        "tax_percentage":tax_pct,
        "tax_amount":    tax_amount,
        "shipping_charge":shipping,
        "total_amount":  total,
    }


@inv_ns.route("/")
class InvoiceList(Resource):
    @inv_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        page     = int(request.args.get("page", 1))
        per_page = min(int(request.args.get("per_page", 20)), 100)
        status   = request.args.get("status")
        q = Invoice.query if _is_admin(user) else Invoice.query.filter_by(created_by=uid)
        if status:
            q = q.filter_by(status=status)
        pag = q.order_by(Invoice.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        return {
            "items":    [i.to_dict() for i in pag.items],
            "total":    pag.total,
            "page":     pag.page,
            "pages":    pag.pages,
            "per_page": per_page,
        }

    @inv_ns.doc(security="Bearer")
    @jwt_required()
    def post(self):
        uid  = int(get_jwt_identity())
        data = request.get_json() or {}
        totals = _compute_totals(data)

        due = None
        if data.get("due_date"):
            try:
                due = datetime.fromisoformat(data["due_date"])
            except Exception:
                pass
        if not due:
            due = datetime.utcnow() + timedelta(days=30)

        inv = Invoice(
            invoice_number   = Invoice.generate_number(),
            shipment_id      = data.get("shipment_id"),
            packing_list_id  = data.get("packing_list_id"),
            created_by       = uid,
            seller_name      = data.get("seller_name", "").strip(),
            seller_address   = data.get("seller_address", ""),
            seller_phone     = data.get("seller_phone", ""),
            seller_country   = data.get("seller_country", "Nepal"),
            seller_tin       = data.get("seller_tin", ""),
            buyer_name       = data.get("buyer_name", "").strip(),
            buyer_address    = data.get("buyer_address", ""),
            buyer_phone      = data.get("buyer_phone", ""),
            buyer_country    = data.get("buyer_country", ""),
            buyer_tin        = data.get("buyer_tin", ""),
            items            = data.get("items", []),
            currency         = data.get("currency", "USD"),
            payment_terms    = data.get("payment_terms", "Prepaid"),
            incoterms        = data.get("incoterms", ""),
            port_of_loading  = data.get("port_of_loading", ""),
            port_of_discharge= data.get("port_of_discharge", ""),
            notes            = data.get("notes", ""),
            status           = "draft",
            due_date         = due,
            **totals,
        )
        db.session.add(inv)
        db.session.commit()
        return inv.to_dict(), 201


@inv_ns.route("/search")
class InvoiceSearch(Resource):
    @inv_ns.doc(security="Bearer")
    @jwt_required()
    def get(self):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        q_str = request.args.get("q", "").strip()
        base  = Invoice.query if _is_admin(user) else Invoice.query.filter_by(created_by=uid)
        if q_str:
            like = f"%{q_str}%"
            base = base.filter(db.or_(
                Invoice.seller_name.ilike(like),
                Invoice.buyer_name.ilike(like),
                Invoice.invoice_number.ilike(like),
            ))
        results = base.order_by(Invoice.created_at.desc()).limit(50).all()
        return [i.to_dict() for i in results]


@inv_ns.route("/<int:id>")
class InvoiceDetail(Resource):
    @inv_ns.doc(security="Bearer")
    @jwt_required()
    def get(self, id):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        inv  = Invoice.query.get_or_404(id)
        if not _own_or_admin(inv, user):
            inv_ns.abort(403, "Access denied")
        return inv.to_dict()

    @inv_ns.doc(security="Bearer")
    @jwt_required()
    def put(self, id):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        inv  = Invoice.query.get_or_404(id)
        if not _own_or_admin(inv, user):
            inv_ns.abort(403, "Access denied")
        if inv.status not in ("draft",) and not _is_admin(user):
            inv_ns.abort(400, "Only draft invoices can be edited")

        data = request.get_json() or {}
        updatable = [
            "seller_name","seller_address","seller_phone","seller_country","seller_tin",
            "buyer_name","buyer_address","buyer_phone","buyer_country","buyer_tin",
            "items","currency","payment_terms","incoterms","port_of_loading",
            "port_of_discharge","notes","status","shipment_id","packing_list_id",
            "discount","tax_percentage","shipping_charge",
        ]
        for field in updatable:
            if field in data:
                setattr(inv, field, data[field])

        totals = _compute_totals({"items": inv.items or [], "discount": inv.discount,
                                   "tax_percentage": inv.tax_percentage, "shipping_charge": inv.shipping_charge})
        for k, v in totals.items():
            setattr(inv, k, v)

        if data.get("due_date"):
            try: inv.due_date = datetime.fromisoformat(data["due_date"])
            except: pass

        db.session.commit()
        return inv.to_dict()

    @inv_ns.doc(security="Bearer")
    @jwt_required()
    def delete(self, id):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        inv  = Invoice.query.get_or_404(id)
        if not _own_or_admin(inv, user):
            inv_ns.abort(403, "Access denied")
        if inv.status not in ("draft",) and not _is_admin(user):
            inv_ns.abort(400, "Only draft invoices can be deleted")
        db.session.delete(inv)
        db.session.commit()
        return {"msg": "Deleted"}


@inv_ns.route("/<int:id>/generate")
class InvoiceGenerate(Resource):
    @inv_ns.doc(security="Bearer")
    @jwt_required()
    def post(self, id):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        inv  = Invoice.query.get_or_404(id)
        if not _own_or_admin(inv, user):
            inv_ns.abort(403, "Access denied")

        from app.utils.excel import generate_invoice_excel
        storage = _storage_dir()
        fpath, fname = generate_invoice_excel(inv, storage)
        inv.file_path = fpath
        inv.file_name = fname
        db.session.commit()
        return {"msg": "Excel generated", "file_name": fname}


@inv_ns.route("/<int:id>/download")
class InvoiceDownload(Resource):
    @inv_ns.doc(security="Bearer")
    @jwt_required()
    def get(self, id):
        uid  = int(get_jwt_identity())
        user = User.query.get_or_404(uid)
        inv  = Invoice.query.get_or_404(id)
        if not _own_or_admin(inv, user):
            inv_ns.abort(403, "Access denied")
        if not inv.file_path or not os.path.exists(inv.file_path):
            inv_ns.abort(404, "Excel not generated yet. POST to /generate first.")
        return send_file(
            inv.file_path,
            as_attachment=True,
            download_name=inv.file_name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
