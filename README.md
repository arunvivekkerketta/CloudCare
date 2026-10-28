# CloudCare

AI-powered hospital management demo: a FastAPI + PostgreSQL backend with a
static multi-role frontend (patient, doctor, nurse, pharmacist, lab, admin).

## Repository layout

```
cloudcare-api/    FastAPI backend (routers, models, Alembic migrations, tests)
cloudcare/        Static frontend (HTML/JS, Netlify-ready)
render.yaml       Render blueprint (API + managed Postgres)
netlify.toml      Netlify config (static site + security headers)
.github/          CI workflow (migrations + pytest)
```

## Features

- **JWT auth** — `POST /api/auth/login` returns a 30-minute bearer token;
  `GET /api/auth/me` validates it. Passwords hashed with **Argon2id**
  (legacy pbkdf2 hashes still verify and are rehashed on login).
- **Safe registration** — public `/api/auth/register` always creates
  `role=patient` with a linked patient profile. Staff accounts are created by
  an admin via `POST /api/admin/users` or the CLI (below).
- **Server-authoritative roles** — sign-in is decided by email + password
  alone (the login form has no role picker). Redirects always follow the role
  the server returns, so users always land on their own dashboard.
- **Scoped access** — patients see only their own profile/appointments/bills;
  doctors see only assigned patients; nurse/pharmacist/lab get read-only
  clinic-wide feeds; admin gets full read + user management + audit logs.
- **Audit trail** — login success/failure, registration, and admin actions
  are recorded in `audit_logs`.
- **Hardened** — rate-limited login/register (slowapi), generic login errors,
  security headers, no exception leakage, CORS allow-list, `/docs` disabled
  in production, liveness `/health` + readiness `/health/db`.
- **Sessions you can keep** — 30-minute tokens by default, "Keep me signed in"
  issues a 7-day token, and `POST /api/auth/refresh` silently renews a token
  that is close to expiring without logging the tab out.
- **Password reset by email** — `POST /api/auth/forgot-password` mails a
  30-minute single-use signed link (`reset-password.html`) through SMTP.
  With no `SMTP_HOST`, development logs the link at INFO and production
  withholds it (WARNING) instead of pretending it was sent.
- **In-app notifications** — bookings, confirmations, declines, leave and
  expiry all write a `notifications` row; the bell in the patient and doctor
  dashboards polls for them and toasts what is new. No email transport is
  required for this part.
- **Appointment lifecycle** — `Requested → Confirmed → Completed` (plus
  `Declined`, `Cancelled`, `Expired`), with transitions validated server-side,
  stale requests auto-expired on read, and one invoice generated per
  completed visit.
- **Patient Health Risk** — the patient dashboard's **Health risk** tab runs
  the bundled multi-disease model (type 2 diabetes, heart disease, kidney
  disease, stroke) on the patient's own readings. The result is explained in
  plain language and the composite score is saved to the patient's record.
  The model files ship in `cloudcare-api/agents/patient_health_risk/`, so the
  feature works on Render without a separate service.
- **Smart Inventory Management System** — an admin-only **Inventory** tab
  forecasts the next 30 days from live stock levels and audited stock
  movements, drafts reorder quantities, flags expired/expiring stock, and
  explains every suggestion. Admins add items and record stock movements from
  the same tab; nothing is ordered automatically.

## Local setup

Requirements: Python 3.12 (3.11+ works; 3.14 is not supported by some pinned
binary wheels).

**Windows shortcut:** double-click `start.bat` — it runs the migrations, starts
both servers in separate windows, and opens the site in your browser.

```bash
cd cloudcare-api
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env

# Migrations + demo data (SQLite by default in dev)
alembic upgrade head
python -m app.scripts.seed_demo

# Run the API
uvicorn app.main:app --reload
```

The API is now at `http://127.0.0.1:8000`. Open `cloudcare/index.html` with a
static server (e.g. `npx serve cloudcare` or VS Code Live Server on port 5500)
and sign in with any seeded account, e.g. `ritu.malhotra@cloudcare.demo` /
`Demo!Pass123` (admin).

Demo accounts all use password `Demo!Pass123` — see
`app/scripts/seed_demo.py` for the full list.

### First admin (Postgres/production)

```bash
python -m app.scripts.create_admin
```

## Tests

```bash
cd cloudcare-api
pytest -v
```

The suite covers: registration cannot create admins, expired/garbage/missing
tokens return 401, wrong password, role mismatch, patient A cannot read
patient B's appointments or bills, doctor/admin/staff scoping, audit-log
recording, and the health endpoints.

## Configuration

All variables are documented in `cloudcare-api/.env.example`. Key ones:

| Variable | Purpose |
| --- | --- |
| `ENV` | `production` enables hard guards (real SECRET_KEY, Postgres, no docs) |
| `SECRET_KEY` | JWT signing — required in production |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime (default 30) |
| `REMEMBER_ME_EXPIRE_MINUTES` | "Keep me signed in" token lifetime (default 10080 = 7 days) |
| `DATABASE_URL` | Single Postgres URL (`postgres://` normalized, `sslmode=require` supported) |
| `DB_*` | Fallback when no `DATABASE_URL` is set |
| `CORS_ORIGINS` | Comma-separated allow-list — never `*` |
| `SMTP_HOST` · `SMTP_PORT` · `SMTP_USER` · `SMTP_PASSWORD` · `SMTP_FROM` | Password-reset email (STARTTLS). Unset = log only, never a silent failure |
| `FRONTEND_URL` | Public frontend origin used inside emailed reset links |
| `HEALTH_RISK_AGENT_DIR` | Optional path to a different `Patient Health Risk/AI Agents` folder. Leave unset to use the bundled models, or point it at a missing folder to disable the assistant (all `/api/health-risk` routes then return 503). |

## Deploy

- **API + Postgres:** Render blueprint (`render.yaml`) — see `DEPLOY.md`
  for click-by-click steps.
- **Frontend:** Netlify (`netlify.toml`, publish dir is the inner
  `cloudcare/` folder).
- Full instructions + smoke-test checklist: **[DEPLOY.md](DEPLOY.md)**.

## API surface

| Method & path | Who |
| --- | --- |
| `POST /api/auth/register` | public (always patient) |
| `POST /api/auth/login` | public (rate-limited) |
| `GET /api/auth/me` | any authenticated |
| `POST /api/auth/change-password` | any authenticated (rotates the token) |
| `POST /api/auth/forgot-password` | public (rate-limited, always the same 200) |
| `POST /api/auth/reset-password` | public (single-use signed token) |
| `POST /api/auth/refresh` | any authenticated (renews the current token) |
| `GET /api/notifications` · `POST /api/notifications/read` | any authenticated (own rows) |
| `GET /api/appointments?scope=upcoming\|past\|all` | scoped per role, `X-Total-Count` header |
| `GET /api/patients/me` · `PATCH /api/patients/me` | patient (self only) |
| `GET /api/reviews` | public (paged, newest first) |
| `GET /api/reviews/eligible-doctors` | patient (doctors they actually visited) |
| `GET /api/patients` | doctor (assigned) · admin (all) |
| `GET/POST/PATCH /api/appointments` | scoped per role |
| `GET /api/bills` · `PATCH /api/bills/{id}` | patient (own) · staff (read) · admin (update) |
| `POST /api/health-risk/predict` | patient (self) · admin |
| `GET /api/inventory/insights` | admin (Smart Inventory forecasts) |
| `POST/PATCH/DELETE /api/admin/users` | admin |
| `GET /api/users/summary` · `GET /api/admin/audit-logs` | admin |
| `GET /health` · `GET /health/db` | public |
