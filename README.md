# Courier Nepal backend

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

The existing registration behavior assigns the first account in an empty database the super_admin role; later registrations are customers. Establish the administrator account before opening registration.

## Verification

```powershell
.venv/Scripts/python -m pip install pytest
.venv/Scripts/python -m pytest -q --disable-warnings
```

tests/test_frontend_contract.py verifies cookie login, CSRF enforcement, booking, listing, public tracking, profile updates, contact submission, role checks, refresh and logout. All 37 tests passed during this update.

## Deployment

Use .env.example for production MySQL and SMTP settings. Set FLASK_ENV=production and deploy behind HTTPS with a production WSGI server. Set FRONTEND_URL to the public frontend origin for password-reset links. Set Next.js BACKEND_URL to this service origin, without /api. Configure RATELIMIT_STORAGE_URI for shared rate limiting when running multiple workers.

SMTP delivery, production MySQL and external carriers require real deployment configuration and were not tested locally. Configure pricing rules before expecting estimates. No payments are taken by the booking flow.

The local preview database contains development-only QA accounts and shipments. Use a separate production database; do not deploy the preview SQLite database.
