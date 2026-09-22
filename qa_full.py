"""
Full-system QA harness for the Moonlight Freight API.
Exercises every namespace end-to-end against a running server (localhost:5000).
Creates dedicated test users (super_admin, agent, customer), runs tests,
then cleans up all data it created.
"""
import sys
import uuid
import requests

BASE = "http://localhost:5000/api"
S = requests.Session()
S.headers.update({"Content-Type": "application/json"})

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, info=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}: {info}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name}: {info}")


def hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ── Bootstrap dedicated test users directly in the DB ───────────────────────────
def bootstrap_users():
    from app import create_app
    from app.extensions import db
    from app.models.user import User, UserStatus
    from app.models.role import Role

    app = create_app("development")
    out = {}
    with app.app_context():
        specs = [
            ("qa_super@test.local", "super_admin", "QaPass123!"),
            ("qa_agent@test.local", "agent", "QaPass123!"),
            ("qa_cust@test.local", "customer", "QaPass123!"),
        ]
        for email, role, pw in specs:
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(
                    username=email.split("@")[0],
                    email=email,
                    first_name="QA",
                    last_name=role.title(),
                    status=UserStatus.ACTIVE,
                )
                u.set_password(pw)
                r = Role.query.filter_by(name=role).first()
                if r:
                    u.roles.append(r)
                db.session.add(u)
                db.session.commit()
            out[role] = {"email": email, "password": pw, "id": u.id}
    return out


def cleanup_users(emails):
    from app import create_app
    from app.extensions import db
    from app.models.user import User
    from app.models.shipment import Shipment
    from app.models.invoice import Invoice
    from app.models.packing_list import PackingList
    app = create_app("development")
    with app.app_context():
        for email in emails:
            u = User.query.filter_by(email=email).first()
            if not u:
                continue
            # remove any business rows so the user row can be deleted
            for s in Shipment.query.filter_by(user_id=u.id).all():
                db.session.delete(s)
            for inv in Invoice.query.filter_by(created_by=u.id).all():
                db.session.delete(inv)
            for pl in PackingList.query.filter_by(created_by=u.id).all():
                db.session.delete(pl)
            db.session.flush()
            db.session.delete(u)
        db.session.commit()


def login(email, pw):
    r = S.post(f"{BASE}/auth/login", json={"email": email, "password": pw})
    if r.status_code != 200:
        print(f"  LOGIN FAILED for {email}: {r.status_code} {r.text[:200]}")
        return None
    return r.json().get("access_token")


def main():
    print("=== BOOTSTRAP USERS ===")
    users = bootstrap_users()
    admin_tok = login(users["super_admin"]["email"], users["super_admin"]["password"])
    agent_tok = login(users["agent"]["email"], users["agent"]["password"])
    cust_tok = login(users["customer"]["email"], users["customer"]["password"])
    A = hdr(admin_tok)
    C = hdr(cust_tok)
    check("admin login", admin_tok is not None)
    check("agent login", agent_tok is not None)
    check("customer login", cust_tok is not None)

    # ── AUTH ────────────────────────────────────────────────────────────────────
    print("\n=== AUTH ===")
    r = S.get(f"{BASE}/auth/profile", headers=A)
    check("GET /auth/profile", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/auth/profile")
    check("GET /auth/profile no auth -> 401", r.status_code == 401, f"HTTP {r.status_code}")
    r = S.post(f"{BASE}/auth/login", json={"email": users["customer"]["email"], "password": "wrong"})
    check("POST /auth/login bad creds -> 401", r.status_code == 401, f"HTTP {r.status_code}")
    new_email = f"qa_reg_{uuid.uuid4().hex[:6]}@test.local"
    r = S.post(f"{BASE}/auth/register", json={"email": new_email, "password": "RegPass123!", "first_name": "Reg", "last_name": "User"})
    check("POST /auth/register", r.status_code == 201, f"HTTP {r.status_code}")
    r = S.post(f"{BASE}/auth/register", json={"email": new_email, "password": "RegPass123!", "first_name": "Reg", "last_name": "User"})
    check("POST /auth/register dup -> 409", r.status_code == 409, f"HTTP {r.status_code}")
    r = S.post(f"{BASE}/auth/register", json={"email": f"weak_{uuid.uuid4().hex[:6]}@test.local", "password": "weak", "first_name": "W", "last_name": "K"})
    check("POST /auth/register weak pw -> 400", r.status_code == 400, f"HTTP {r.status_code}")

    # ── SERVICES (public read) ───────────────────────────────────────────────────
    print("\n=== SERVICES ===")
    r = S.get(f"{BASE}/services/")
    check("GET /services public", r.status_code == 200, f"HTTP {r.status_code}, n={len(r.json()) if r.ok else '?'}")
    r = S.post(f"{BASE}/services/", headers=A, json={"code": f"QA_{uuid.uuid4().hex[:5]}", "name": "QA Service", "delivery_time": "1d", "price_range": "Rs.1"})
    check("POST /services admin", r.status_code in (200, 201), f"HTTP {r.status_code}")
    svc_id = r.json().get("id") if r.ok else None
    if svc_id:
        r = S.get(f"{BASE}/services/{svc_id}")
        check("GET /services/{id}", r.status_code == 200, f"HTTP {r.status_code}")
        r = S.delete(f"{BASE}/services/{svc_id}", headers=A)
        check("DELETE /services/{id}", r.status_code in (200, 204), f"HTTP {r.status_code}")

    # ── FAQ ──────────────────────────────────────────────────────────────────────
    print("\n=== FAQ ===")
    r = S.get(f"{BASE}/faq/")
    check("GET /faq public", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.post(f"{BASE}/faq/", headers=A, json={"question": f"QA Q {uuid.uuid4().hex[:5]}?", "answer": "A", "category": "general", "order": 99})
    check("POST /faq admin", r.status_code in (200, 201), f"HTTP {r.status_code}")
    faq_id = r.json().get("id") if r.ok else None
    if faq_id:
        r = S.delete(f"{BASE}/faq/{faq_id}", headers=A)
        check("DELETE /faq/{id}", r.status_code in (200, 204), f"HTTP {r.status_code}")

    # ── WAREHOUSES ─────────────────────────────────────────────────────────────--
    print("\n=== WAREHOUSES ===")
    r = S.get(f"{BASE}/warehouses/")
    check("GET /warehouses public", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.post(f"{BASE}/warehouses/", headers=A, json={"name": f"QA WH {uuid.uuid4().hex[:5]}", "code": f"QAWH{uuid.uuid4().hex[:5].upper()}", "location": "KTM", "country": "Nepal"})
    check("POST /warehouses admin", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:120]}")
    r = S.post(f"{BASE}/warehouses/", headers=A, json={"location": "no name/code"})
    check("POST /warehouses missing required -> 400", r.status_code == 400, f"HTTP {r.status_code}")
    wh_id = r.json().get("id") if r.ok else None
    if wh_id:
        r = S.delete(f"{BASE}/warehouses/{wh_id}", headers=A)
        check("DELETE /warehouses/{id}", r.status_code in (200, 204), f"HTTP {r.status_code}")

    # ── CONTACT ──────────────────────────────────────────────────────────────────
    print("\n=== CONTACT ===")
    r = S.post(f"{BASE}/contact/", json={"name": "QA", "email": "qa@x.com", "subject": "Hi", "message": "Test message"})
    check("POST /contact public", r.status_code in (200, 201), f"HTTP {r.status_code}")
    contact_id = r.json().get("id") if r.ok else None
    r = S.get(f"{BASE}/contact/", headers=A)
    check("GET /contact admin", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/contact/")
    check("GET /contact no auth -> 401", r.status_code == 401, f"HTTP {r.status_code}")
    if contact_id:
        r = S.delete(f"{BASE}/contact/{contact_id}", headers=A)
        check("DELETE /contact/{id}", r.status_code in (200, 204), f"HTTP {r.status_code}")

    # ── PRICING ──────────────────────────────────────────────────────────────────
    print("\n=== PRICING ===")
    r = S.get(f"{BASE}/pricing/")
    check("GET /pricing public", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.post(f"{BASE}/pricing/calculate", json={"service_type": "international", "shipment_type": "standard", "origin_country": "Nepal", "destination_country": "India", "weight": 5, "declared_value": 100})
    check("POST /pricing/calculate", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    r = S.get(f"{BASE}/pricing/rules", headers=A)
    check("GET /pricing/rules admin (was broken role guard)", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/pricing/rules", headers=C)
    check("GET /pricing/rules customer -> 403", r.status_code == 403, f"HTTP {r.status_code}")

    # ── SHIPMENTS ──────────────────────────────────────────────────────────────--
    print("\n=== SHIPMENTS ===")
    ship_body = {
        "origin": "Kathmandu", "destination": "Delhi", "destination_country": "India",
        "sender_name": "QA Sender", "receiver_name": "QA Receiver",
        "declared_value": 100, "total_weight_kg": 5, "total_packages": 2,
        "parcels": [{"weight_kg": 2.5, "description": "box1"}, {"weight_kg": 2.5, "description": "box2"}],
    }
    r = S.post(f"{BASE}/shipments/", headers=C, json=ship_body)
    check("POST /shipments (customer)", r.status_code == 201, f"HTTP {r.status_code} {r.text[:120]}")
    ship = r.json() if r.ok else {}
    ship_id = ship.get("id")
    tracking_no = ship.get("tracking_no")
    check("shipment has parcels", len(ship.get("parcels", [])) == 2, f"n={len(ship.get('parcels', []))}")
    check("shipment has events", len(ship.get("events", [])) >= 1, f"n={len(ship.get('events', []))}")
    r = S.get(f"{BASE}/shipments/my", headers=C)
    check("GET /shipments/my", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/shipments/", headers=A)
    check("GET /shipments admin list", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/shipments/", headers=C)
    check("GET /shipments customer -> 403", r.status_code == 403, f"HTTP {r.status_code}")
    if ship_id:
        r = S.get(f"{BASE}/shipments/{ship_id}", headers=C)
        check("GET /shipments/{id} owner", r.status_code == 200, f"HTTP {r.status_code}")
        r = S.put(f"{BASE}/shipments/{ship_id}", headers=A, json={"status": "in_transit", "location": "Border", "description": "Moving"})
        check("PUT /shipments/{id} status (admin)", r.status_code == 200, f"HTTP {r.status_code}")
    if tracking_no:
        r = S.get(f"{BASE}/shipments/tracking/{tracking_no}", headers=C)
        check("GET /shipments/tracking/{no}", r.status_code == 200, f"HTTP {r.status_code}")

    # ── TRACKING (public) ──────────────────────────────────────────────────────--
    print("\n=== TRACKING ===")
    if tracking_no:
        r = S.get(f"{BASE}/tracking/{tracking_no}")
        check("GET /tracking/{no} public", r.status_code == 200, f"HTTP {r.status_code}")
        r = S.post(f"{BASE}/tracking/{tracking_no}/events", headers=hdr(agent_tok),
                   json={"status": "out_for_delivery", "location": "Delhi Hub", "description": "On the way"})
        check("POST /tracking/{no}/events (agent)", r.status_code == 201, f"HTTP {r.status_code} {r.text[:120]}")
        r = S.post(f"{BASE}/tracking/{tracking_no}/events", headers=C,
                   json={"status": "x", "location": "y"})
        check("POST /tracking/{no}/events customer -> 403", r.status_code == 403, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/tracking/NONEXISTENT123")
    check("GET /tracking/bad -> 404", r.status_code == 404, f"HTTP {r.status_code}")

    # ── TRACKING PROVIDERS ───────────────────────────────────────────────────────
    print("\n=== TRACKING PROVIDERS ===")
    r = S.get(f"{BASE}/tracking-providers/")
    check("GET /tracking-providers public", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/tracking-providers/all", headers=A)
    check("GET /tracking-providers/all admin", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/tracking-providers/all", headers=C)
    check("GET /tracking-providers/all customer -> 403", r.status_code == 403, f"HTTP {r.status_code}")

    # ── INVOICES ─────────────────────────────────────────────────────────────────
    print("\n=== INVOICES ===")
    inv_body = {
        "seller_name": "QA Seller", "buyer_name": "QA Buyer", "buyer_country": "India",
        "items": [{"description": "Item1", "quantity": 2, "unit_price": 50, "total_price": 100}],
        "tax_percentage": 13, "shipping_charge": 20, "currency": "USD",
    }
    r = S.post(f"{BASE}/invoices/", headers=C, json=inv_body)
    check("POST /invoices", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:120]}")
    inv = r.json() if r.ok else {}
    inv_id = inv.get("id")
    check("invoice totals computed", abs(inv.get("total_amount", 0) - 133.0) < 0.01, f"total={inv.get('total_amount')}")
    r = S.get(f"{BASE}/invoices/", headers=C)
    check("GET /invoices", r.status_code == 200, f"HTTP {r.status_code}")
    if inv_id:
        r = S.get(f"{BASE}/invoices/{inv_id}", headers=C)
        check("GET /invoices/{id} owner", r.status_code == 200, f"HTTP {r.status_code}")
        r = S.get(f"{BASE}/invoices/{inv_id}", headers=hdr(agent_tok))
        check("GET /invoices/{id} other-user -> 403/404", r.status_code in (403, 404), f"HTTP {r.status_code}")
        r = S.delete(f"{BASE}/invoices/{inv_id}", headers=C)
        check("DELETE /invoices/{id}", r.status_code in (200, 204), f"HTTP {r.status_code}")

    # ── PACKING LISTS ──────────────────────────────────────────────────────────--
    print("\n=== PACKING LISTS ===")
    pl_body = {
        "sender_name": "QA S", "receiver_name": "QA R", "receiver_country": "India",
        "items": [{"description": "x", "quantity": 3, "unit_weight_kg": 1.5, "total_weight_kg": 4.5}],
    }
    r = S.post(f"{BASE}/packing-lists/", headers=C, json=pl_body)
    check("POST /packing-lists", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:120]}")
    pl = r.json() if r.ok else {}
    pl_id = pl.get("id")
    r = S.get(f"{BASE}/packing-lists/", headers=C)
    check("GET /packing-lists", r.status_code == 200, f"HTTP {r.status_code}")
    if pl_id:
        r = S.delete(f"{BASE}/packing-lists/{pl_id}", headers=C)
        check("DELETE /packing-lists/{id}", r.status_code in (200, 204), f"HTTP {r.status_code}")

    # ── AGENTS ───────────────────────────────────────────────────────────────────
    print("\n=== AGENTS ===")
    r = S.get(f"{BASE}/agents/", headers=A)
    check("GET /agents admin", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/agents/")
    check("GET /agents no auth -> 401", r.status_code == 401, f"HTTP {r.status_code}")

    # ── USERS ────────────────────────────────────────────────────────────────────
    print("\n=== USERS ===")
    r = S.get(f"{BASE}/users/", headers=A)
    check("GET /users admin", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/users/", headers=C)
    check("GET /users customer -> 403", r.status_code == 403, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/users/{users['customer']['id']}", headers=C)
    check("GET /users/{id} self", r.status_code == 200, f"HTTP {r.status_code}")

    # ── SESSIONS ─────────────────────────────────────────────────────────────────
    print("\n=== SESSIONS ===")
    r = S.get(f"{BASE}/sessions/", headers=A)
    check("GET /sessions", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/sessions/")
    check("GET /sessions no auth -> 401", r.status_code == 401, f"HTTP {r.status_code}")

    # ── AUDIT ────────────────────────────────────────────────────────────────────
    print("\n=== AUDIT ===")
    r = S.get(f"{BASE}/audit/logs/", headers=A)
    check("GET /audit/logs admin", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/audit/logs/me/", headers=C)
    check("GET /audit/logs/me", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/audit/logs/")
    check("GET /audit/logs no auth -> 401", r.status_code == 401, f"HTTP {r.status_code}")

    # ── BLOG (smoke) ─────────────────────────────────────────────────────────────
    print("\n=== BLOG ===")
    r = S.get(f"{BASE}/blog/categories")
    check("GET /blog/categories", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.post(f"{BASE}/blog/categories", headers=A, json={"name": f"QA Cat {uuid.uuid4().hex[:5]}"})
    check("POST /blog/categories admin", r.status_code in (200, 201), f"HTTP {r.status_code}")
    cat = r.json() if r.ok else {}
    cat_id = cat.get("id")
    r = S.get(f"{BASE}/blog/posts?status=all")
    check("GET /blog/posts", r.status_code == 200, f"HTTP {r.status_code}")
    if cat_id:
        r = S.post(f"{BASE}/blog/posts", headers=A, json={"title": f"QA Post {uuid.uuid4().hex[:5]}", "content": "Body", "category_id": cat_id, "status": "published"})
        check("POST /blog/posts admin", r.status_code == 201, f"HTTP {r.status_code} {r.text[:120]}")
        slug = r.json().get("slug") if r.ok else None
        if slug:
            r = S.delete(f"{BASE}/blog/posts/{slug}", headers=A)
            check("DELETE /blog/posts/{slug}", r.status_code in (200, 204), f"HTTP {r.status_code}")
        r = S.delete(f"{BASE}/blog/categories/{cat.get('slug')}", headers=A)

    # ── ADMIN DASHBOARD (was 500 due to missing is_admin) ────────────────────────
    print("\n=== ADMIN DASHBOARD ===")
    r = S.get(f"{BASE}/admin/dashboard/stats", headers=A)
    check("GET /admin/dashboard/stats (was is_admin 500)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    r = S.get(f"{BASE}/admin/dashboard/shipment-trends", headers=A)
    check("GET /admin/dashboard/shipment-trends", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/admin/dashboard/revenue-analytics", headers=A)
    check("GET /admin/dashboard/revenue-analytics", r.status_code == 200, f"HTTP {r.status_code}")
    r = S.get(f"{BASE}/admin/dashboard/stats", headers=C)
    check("GET /admin/dashboard/stats customer -> 403", r.status_code == 403, f"HTTP {r.status_code}")

    # ── CLEANUP ──────────────────────────────────────────────────────────────────
    print("\n=== CLEANUP ===")
    try:
        if ship_id:
            S.delete(f"{BASE}/shipments/{ship_id}", headers=A)
        cleanup_users([u["email"] for u in users.values()] + [new_email])
        print("  Done.")
    except Exception as e:
        print(f"  Cleanup warning: {e}")

    print("\n" + "=" * 62)
    print(f"  RESULTS:  {PASS} PASSED  /  {FAIL} FAILED  /  {PASS + FAIL} TOTAL")
    if FAILED:
        print("  FAILED:")
        for f in FAILED:
            print(f"    - {f}")
    print("=" * 62)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
