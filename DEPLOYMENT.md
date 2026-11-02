# Courier Nepal: local setup and cPanel deployment

Production domain: **https://couriernepal.com**. The frontend is a Node.js Next.js server; the backend is a Python Flask application. This is not a static HTML/PHP upload.

## 1. Local setup (Windows PowerShell)

Install Node.js 22.23.4 (or a newer patched 22.x release), Python 3.12 or 3.13, and Git. Clone the repositories next to each other:

```powershell
git clone https://github.com/Roshan-234/couriernepalbackend.git
git clone https://github.com/Roshan-234/couriernepalfrontend.git
cd couriernepalbackend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest
Copy-Item .env.local.example .env
.venv/Scripts/python -c "import secrets; print(secrets.token_hex(32))"
```

Generate a separate random value for `SECRET_KEY` and `JWT_SECRET_KEY`; put them in backend `.env`. Do not commit `.env`. Development uses SQLite and creates missing tables/initial page content. It does not invent commercial shipping rates. Start the backend:

```powershell
.venv/Scripts/python -m flask --app run create-admin
.venv/Scripts/python -m flask --app run run --host 127.0.0.1 --port 5000
```

In a second terminal:

```powershell
cd couriernepalfrontend
npm ci
Copy-Item .env.example .env.local
npm run dev
```

Open http://localhost:3000. Sign in with the administrator you created. Configure real shipping rates in `/admin/pricing`; edit public content and SEO in `/admin/content`. Registration creates customers, never administrators. Public page content, metadata and service descriptions are DB managed; account forms and operational UI are code-managed and use authenticated business APIs.

Run checks from the respective repository:

```powershell
# Backend
.venv/Scripts/python -m pytest -q --disable-warnings
.venv/Scripts/python scripts/verify_migrations.py
# Frontend
npm run lint
npm run build
npm run typecheck
npm start
```

On constrained Windows hosts, set `$env:NEXT_LOW_MEMORY_BUILD='1'`. To isolate a stale build directory, set `$env:NEXT_DIST_DIR='.next-release'` for **both** build and start. Do not run a build against a directory currently used by a running server.

Use the declared Node version (`.nvmrc` and `package.json` engines). The full local production build and runtime are verified on Node 22.23.4; the older system Node 24.12.0 produced streaming cancellation errors. Do not assume any older Node patch is interchangeable.

## 2. Confirm cPanel capabilities before deployment

Your host must provide Node.js 22.23.4 or newer 22.x, Python 3.12+, Passenger/Application Manager (or CloudLinux “Setup Node.js App” and “Setup Python App”), MySQL 8 or compatible MariaDB, HTTPS certificates and shared Redis storage. Ask your host for these features if absent. Some cPanel plans support only PHP: this project cannot run fully on those plans. Redis credentials may come from a TLS-enabled managed Redis provider if your host permits outbound access.

This repository prepares deployment files; it does not provision DNS, certificates, a Redis service, MySQL accounts or SMTP credentials. Do not expose application roots or `.env` files under a static `public_html` directory.

Use two application roots outside `public_html`, for example:

```text
/home/CPANEL_USER/apps/couriernepalfrontend
/home/CPANEL_USER/apps/couriernepalbackend
```

Recommended domains:

- `couriernepal.com`: Node.js frontend, application URL `/`.
- `api.couriernepal.com`: Python backend, application URL `/`.

Create DNS records and valid HTTPS certificates for both. Redirect `www.couriernepal.com` to `couriernepal.com` at the web server. The API URL is an assumption for setup; create it before configuring the frontend.

## 3. Configure the backend

Create a dedicated MySQL database and user in cPanel. Give that user access only to this application's database. Copy backend `.env.example` to an untracked `.env`, or use cPanel environment settings. Set:

```dotenv
FLASK_ENV=production
FRONTEND_URL=https://couriernepal.com
MYSQL_HOST=localhost
MYSQL_USER=YOUR_CPANEL_DATABASE_USER
MYSQL_PASSWORD=YOUR_DATABASE_PASSWORD
MYSQL_DATABASE=YOUR_CPANEL_DATABASE
SECRET_KEY=GENERATE_A_UNIQUE_RANDOM_SECRET
JWT_SECRET_KEY=GENERATE_ANOTHER_RANDOM_SECRET
BACKEND_PROXY_SECRET=GENERATE_A_THIRD_RANDOM_SECRET
RATELIMIT_STORAGE_URI=rediss://REDIS_USER:REDIS_PASSWORD@REDIS_HOST:REDIS_PORT/0
TRUSTED_PROXY_HOPS=0
```

Generate each secret independently with `python -c "import secrets; print(secrets.token_hex(32))"`. Production refuses short/reused secrets, HTTP frontend URLs, SQLite, and process-local rate limiting. `BACKEND_PROXY_SECRET` must also be configured on the frontend server. It is never a `NEXT_PUBLIC_` variable.

Set `TRUSTED_PROXY_HOPS` only after the host documents the trusted reverse-proxy chain and overwrites/appends forwarding headers correctly. Zero is the safe default; it may group users behind a proxy into one rate-limit bucket. Never guess this number to fix a rate-limit problem.

In Setup Python App, select the backend root, Python 3.12+, startup file **passenger_wsgi.py**, callable **application**, and production mode. Activate the virtual environment using the exact command provided by cPanel. Then:

```bash
pip install -r requirements.txt
python -m flask --app run db upgrade
python -m flask --app run seed-content
python -m flask --app run schema-check
python -m flask --app run create-admin
```

The first migration sequence is for a new empty database or a database correctly tracked by Alembic. Production never runs `create_all()` or seeds content during worker startup. `seed-content` is repeatable and preserves edited pages. Configure only real rates; previous project versions automatically seeded sample rates, so review/deactivate any inherited sample rules before opening booking.

**Existing database:** back it up and rehearse on a clone first. Earlier versions created tables outside Alembic. Do not run `stamp head` blindly: a schema may already contain some new tables while lacking others. Compare with the models/migration history and have the database administrator establish the correct baseline. `schema-check` checks table/column presence only; it does not validate types, indexes, constraints or business data. The new migration preserves legacy tracking events by mapping parcel IDs to shipment IDs and stops on orphan references. It is forward-only; rollback means restoring the verified backup.

Configure SMTP host, port, TLS/SSL, username, password and sender using real provider values. Test password-reset delivery to your own account after deployment. The application no longer exposes an unauthenticated mail-test endpoint.

## 4. Configure and build the frontend

In Setup Node.js App/Application Manager, select Node.js 22.23.4 or newer 22.x, production mode, the frontend root and startup file **app.js**. Configure these server variables before building and starting:

```dotenv
NODE_ENV=production
SITE_URL=https://couriernepal.com
BACKEND_URL=https://api.couriernepal.com
BACKEND_PROXY_SECRET=THE_SAME_THIRD_SECRET_AS_BACKEND
ENABLE_HSTS=true
GA_MEASUREMENT_ID=
GOOGLE_SITE_VERIFICATION=
```

`BACKEND_URL` must not include `/api`. Browser calls go to `/api` on the frontend origin. Next.js forwards them to Flask and inserts the server-only shared key. Cookies remain scoped to the frontend domain. Direct backend API requests without the key return 403. Do not add a permissive CORS wildcard to solve proxy problems.

```bash
npm ci
npm run build
mkdir -p tmp
touch tmp/restart.txt
```

Use cPanel's Restart button or Passenger's restart file for each app after changing code/environment. Build on Linux or on the hosting server: do not upload Windows `node_modules` or build artifacts with native Windows dependencies. `app.js` verifies required production settings and uses the Passenger-provided port. If the host disallows the necessary Node/Python services, move these services to a compatible host while keeping DNS under cPanel.

Enable HSTS only after HTTPS works on `couriernepal.com`; the header intentionally does not include subdomains or preload. Request-specific CSP nonces require dynamic rendering. This trades static full-page caching for stronger script controls; keep `/_next/static` cacheable, but never cache authenticated API responses or HTML with per-request nonces in a CDN.

### Optional GitHub deployment

The backend's `Deploy backend to cPanel` workflow is now manual (`workflow_dispatch`), so pushing code cannot unexpectedly migrate a production database. Run it from `main` only after the checks pass, a clone rehearsal succeeds, a verified backup exists and maintenance is enabled. Configure a GitHub `production` environment with appropriate release protection.

Required deployment secrets are `SSH_HOST`, `SSH_USER`, `SSH_PORT`, `SSH_PRIVATE_KEY`, `SSH_KNOWN_HOSTS`, `BACKEND_PATH`, and `VENV_PATH`. `SSH_KNOWN_HOSTS` must contain the server key verified independently with your hosting provider. `VENV_PATH` is the absolute path ending in `/bin/activate`, not a shell command (it replaces the previous `VENV_ACTIVATE` setting). Paths must be below `/home/USER/`. The workflow preserves environment files, runtime storage and virtual environments; it does not delete unknown remote files. Review any obsolete source files during a release. Deploy/restart the frontend separately using the steps above, then perform the release checks before leaving maintenance.

## 5. SEO and optional analytics

The site includes DB-managed titles/descriptions, canonical URLs, Open Graph/Twitter metadata, organization JSON-LD, server-rendered public page/article text, `robots.txt`, and a DB-generated sitemap. Unpublished pages and drafts are excluded; account routes and shipment tracking are marked noindex. Sitemap article export currently supports up to 10,000 published articles; partition into sitemap files before exceeding that limit. No search ranking is guaranteed.

Verify `https://couriernepal.com` in Google Search Console. Put its HTML verification value in `GOOGLE_SITE_VERIFICATION` and submit `https://couriernepal.com/sitemap.xml`.

Set `GA_MEASUREMENT_ID` to your real `G-...` value to enable the consent UI. It stays disabled when unset. The Google tag loads only after acceptance. Manual public-page views exclude query strings, tracking pages, authentication/account routes and form data. In GA4, **disable automatic/enhanced pageview/history measurement, form interactions and other enhanced measurement** for this stream to avoid duplicate events or unintended data collection. Google tag settings must match the code. Validate in GA4 DebugView/Realtime after deployment; no analytics account has been created or configured for you. Privacy text is editable and should reflect your actual operations and retention practices before launch.

## 6. Release verification and operations

1. Verify `/api/health` through the frontend returns 200 without leaking connection details. Direct backend calls without the shared key should return 403.
2. Test registration, sign-in, profile edits, booking, tracking, pricing, admin content saves and sign-out. Check mobile layout and the browser console for CSP errors.
3. Verify an ordinary customer cannot access admin APIs or another customer's shipment/documents. Verify expired/revoked sessions cannot refresh.
4. Confirm sitemap/canonical domain, published content, privacy wording and real prices. Test SMTP and any live carrier provider separately.
5. Keep both app secrets and backups outside public folders. Restrict `.env` filesystem permissions to the app owner. Enable cPanel account MFA, supported runtime updates, hosting WAF rules and error-log monitoring.
6. Schedule encrypted MySQL and document-storage backups, define retention and rehearse restoration. Logs should not collect passwords, tokens or shipment form contents. Rotate credentials if leaked.

**Rollback:** put the site into maintenance, stop both applications, restore the database and document-storage backup, deploy the previous compatible commit/build to both roots, then restart and verify. Do not downgrade the database migration in place. Session changes may require users to sign in again.

## 7. Data structures and performance choices

- Indexed unique tracking numbers and page paths support exact lookups.
- A composite `(user_id, created_at, id)` shipment index supports scoped history ordered deterministically; tracking events index shipment IDs.
- Shipment pagination returns at most 100 rows (UI requests 20); SQL aggregates compute dashboard totals. Search input is bounded and LIKE wildcards are escaped. Route substring search can still scan rows; add a suitable full-text index if profiling shows a bottleneck.
- Server content uses one request-scoped fetch and a `Map` for path lookup, shared by page rendering and metadata.
- Content updates use an atomic version comparison to reject stale edits with 409. Page paths are immutable and editable fields are allowlisted. Content is text/structured sections, never executable templates.
- React Query caches account queries, debounces shipment search and clears account data on login/logout. Blog pagination allocates a bounded number of controls rather than an array of every page.

These choices fit the actual queries; there is no reason to add unrelated algorithms merely to claim use of DSA.

## References

- [cPanel Node.js deployment](https://docs.cpanel.net/knowledge-base/web-services/how-to-install-a-node.js-application/)
- [cPanel Python WSGI deployment](https://docs.cpanel.net/knowledge-base/web-services/how-to-install-a-python-wsgi-application/)
- [Next.js CSP and dynamic rendering](https://nextjs.org/docs/app/guides/content-security-policy)
- [GA4 manual pageviews](https://developers.google.com/analytics/devguides/collection/ga4/views)

No system is hacker-proof. These controls reduce risk; hosting configuration, monitoring, dependency updates, backups and a deployment-specific security review remain necessary.
