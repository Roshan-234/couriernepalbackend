# Moonlight Freight backend

Flask API for the Next.js frontend. Browser traffic stays on the frontend origin, with Next.js forwarding /api/* to Flask. This preserves HttpOnly cookies and CSRF protection.

## Local setup

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

Create an untracked .env with FLASK_ENV=development, FRONTEND_URL=http://localhost:3000, and independently generated SECRET_KEY and JWT_SECRET_KEY values:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate each secret separately. Development uses SQLite in instance/app.db.

```powershell
.venv/Scripts/python -m flask --app run run --host 127.0.0.1 --port 5000
```

Public registration always creates a customer. Bootstrap an administrator locally with the interactive command below (the password is hidden and validated):

```powershell
.venv/Scripts/python -m flask --app run create-admin
```

Existing administrators retain their roles. This update changes session token claims: existing sign-ins must log in again after deployment. Access and refresh tokens now share a revocable session, so logout, session revocation, account suspension and password resets invalidate the appropriate tokens.

## Verification

```powershell
.venv/Scripts/python -m pip install pytest
.venv/Scripts/python -m pytest -q --disable-warnings
```

tests/test_frontend_contract.py verifies cookie login, CSRF enforcement, booking, listing, public tracking, profile updates, contact submission, role checks, refresh and logout. The expanded suite contains 65 tests covering the customer journey, permissions, refresh-token revocation, pricing brackets, document ownership, invoice totals, comments, and administrator bootstrap.

## Deployment

Use .env.example for production MySQL and SMTP settings. Set FLASK_ENV=production and deploy behind HTTPS with a production WSGI server. Set FRONTEND_URL to the public frontend origin for password-reset links. Set Next.js BACKEND_URL to this service origin, without /api. Configure RATELIMIT_STORAGE_URI for shared rate limiting when running multiple workers.

SMTP delivery, production MySQL and external carriers require real deployment configuration and were not tested locally. Configure pricing rules before expecting estimates. No payments are taken by the booking flow.

The local preview database contains development-only QA accounts and shipments. Use a separate production database; do not deploy the preview SQLite database.

## Functional fixes

- Admin-only management for services, warehouses, agents, FAQ and blog publishing; protected drafts and comment ownership.
- Validated shipment statuses, numeric fields, parcel weights and service references. No fabricated seven-day delivery estimate.
- Pricing rules validate bounds and percentages, select the matching weight bracket, and support activation/deactivation.
- MySQL-compatible delivery-time calculation; analytics return null expenses/profit because no expense ledger exists.
- Invoice and packing-list links enforce ownership; item totals are derived on the server. openpyxl is declared for workbook exports.
- Account status validation and protection against disabling your own account or editing a super-admin as an ordinary admin.
- GitHub Actions run the backend test suite on pushes and pull requests.

Live SMTP, carrier integrations and production MySQL still require deployment credentials and operational testing. The automated tests use isolated SQLite; they do not claim production MySQL verification.
