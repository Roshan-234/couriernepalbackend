# Moonlight Freight / Courier Nepal backend

Flask API serving the Next.js frontend for **couriernepal.com**. Browser calls pass through the frontend origin so authentication cookies and CSRF protection work consistently.

## Setup and deployment

Follow [DEPLOYMENT.md](DEPLOYMENT.md) for the complete local and cPanel procedure.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest
Copy-Item .env.local.example .env
# Set independent random SECRET_KEY and JWT_SECRET_KEY values in .env.
.venv/Scripts/python -m flask --app run create-admin
.venv/Scripts/python -m flask --app run run --host 127.0.0.1 --port 5000
```

Development creates SQLite tables and starter content. Production requires MySQL, shared rate-limit storage, HTTPS, and three independent strong secrets. It uses explicit migrations and never creates tables or seeds data during worker startup. Public registration creates customers only.

## Features

- Validated shipment booking, tracking updates, paginated owner-scoped history and SQL dashboard totals.
- Role-protected administration, pricing rules, account status and document ownership.
- HttpOnly cookie login, CSRF, revocable sessions, profile updates and password reset.
- Database-managed public pages and SEO fields with atomic version checks; protected article drafts and comment ownership.
- Shared backend access key, response security headers, rate limits and audit logging.
- Explicit schema migrations preserve legacy parcel tracking history and shipment totals. Repeatable content seeding preserves administrator edits.

## Verification

```powershell
.venv/Scripts/python -m pytest -q --disable-warnings
.venv/Scripts/python scripts/verify_migrations.py
```

The suite covers customer workflows, authorization, CSRF, session revocation, pricing, document ownership, CMS updates and production configuration. The migration verifier uses an empty disposable SQLite database by default; never point it at real application data. GitHub Actions also runs it against a disposable MySQL 8 service.

## Launch requirements

Back up existing databases and rehearse upgrades on a clone. Earlier unversioned schemas need a reviewed baseline; never blindly stamp a migration version. The tracking conversion is forward-only, with backup restoration as rollback. Review inherited sample prices, configure real rates, test SMTP, verify live carrier connections, and review starter editorial/privacy text before opening the site. No payment is collected by the booking flow.

No system is hacker-proof. Keep dependencies, hosting protections, monitoring and tested backups current.
