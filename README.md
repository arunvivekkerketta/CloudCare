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
| `DATABASE_URL` | Single Postgres URL (`postgres://` normalized, `sslmode=require` supported) |
| `DB_*` | Fallback when no `DATABASE_URL` is set |
| `CORS_ORIGINS` | Comma-separated allow-list — never `*` |

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
| `POST /api/auth/change-password` | any authenticated |
| `POST /api/auth/forgot-password` | public (placeholder — no email sent) |
| `GET /api/patients/me` · `PATCH /api/patients/me` | patient (self only) |
| `GET /api/patients` | doctor (assigned) · admin (all) |
| `GET/POST/PATCH /api/appointments` | scoped per role |
| `GET /api/bills` · `PATCH /api/bills/{id}` | patient (own) · staff (read) · admin (update) |
| `POST/PATCH/DELETE /api/admin/users` | admin |
| `GET /api/users/summary` · `GET /api/admin/audit-logs` | admin |
| `GET /health` · `GET /health/db` | public |
