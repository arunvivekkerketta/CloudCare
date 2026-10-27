# CloudCare — Deployment Guide

Click-by-click deployment for the API (Render + managed Postgres) and the
static frontend (Netlify), plus a post-deploy smoke-test checklist.

---

## Part 1 — API + Postgres on Render

### 1. Push the code to GitHub

```bash
git add -A
git commit -m "CloudCare: real auth, scoped APIs, deployment config"
git push origin main
```

### 2. Create the Postgres database

1. Go to <https://dashboard.render.com> and sign in.
2. Click **New +** → **Postgres**.
3. Fill in:
   - **Name:** `cloudcare-db`
   - **Database:** `cloudcare`
   - **User:** `cloudcare`
   - **Plan:** the cheapest available (free tier expires databases after 30 days)
   - **Region:** pick one and remember it — the API service must use the same.
4. Click **Create Database**. Wait until the status shows **Available**.
5. (Or skip all of this: the included `render.yaml` blueprint creates the
   database and wires `DATABASE_URL` automatically — see step 3.)

### 3. Create the API service (blueprint option — recommended)

1. Render dashboard → **New +** → **Blueprint**.
2. Select your GitHub repo. Render reads `render.yaml` and shows two
   resources: `cloudcare-api` (web service) and `cloudcare-db` (Postgres).
3. Click **Apply**. Render provisions both.
4. When the first deploy finishes, open the `cloudcare-api` service →
   **Environment** and confirm these exist:
   - `ENV=production`
   - `SECRET_KEY` (auto-generated)
   - `DATABASE_URL` (linked from the database)
   - `CORS_ORIGINS` — **edit this** to your Netlify URL from Part 2,
     e.g. `https://YOUR-SITE.netlify.app` (comma-separate extra origins if needed)
   - `DB_SSLMODE=require`
   - (optional) password-reset email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
     `SMTP_PASSWORD`, `SMTP_FROM` and `FRONTEND_URL` (your Netlify URL).
     Skip these and the API refuses to invent a reset link: it logs a warning
     and still answers 200, so the `/forgot-password` form stays neutral.

### Password reset email

The `/api/auth/forgot-password` endpoint no longer sends email synchronously:
the SMTP call is handed off to a background task so the HTTP response is fast
and identical for registered and unregistered emails. For the reset link to
actually arrive in production, set these environment variables on the API
service: `SMTP_HOST`, `SMTP_PORT` (587 with STARTTLS), `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM` and `FRONTEND_URL` (the public frontend origin
used in the link). With no `SMTP_HOST`, the reset link is only logged in
development and deliberately withheld in production; the API still returns the
neutral "if that email has an account" message either way.
5. Save; the service redeploys automatically.

### 3b. Proxy headers (real visitor IPs)

The start command in `render.yaml` runs gunicorn's uvicorn worker with
`--forwarded-allow-ips="*"`. That makes uvicorn trust Render's proxy and
replace `request.client` with the real visitor address, so the per-IP rate
limits cannot be dodged with a spoofed `X-Forwarded-For` header. Running the
API with plain uvicorn instead? Use the same flags:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
```

The local `start.bat` already passes them. Do not trust a raw
`X-Forwarded-For` header in application code.

### 4. Verify the migration ran

1. Open the `cloudcare-api` service → **Events** tab.
2. The deploy should show the pre-deploy command `alembic upgrade head`
   succeeding before the service goes live.
3. Open **Logs** and look for no `Configuration error` lines. If the service
   exits immediately with a configuration error, fix the env var it names
   (most often: `SECRET_KEY` left default — the blueprint generates one, so
   this only happens with manual env setup).
4. The same pre-deploy command applies every migration, including
   `004_fixes.py` (appointment `status_reason`/`meeting_url`, the doctor
   `consultation_fee`, `bills.appointment_id` and the `notifications` table).
   It runs on both SQLite (local) and PostgreSQL; run it by hand with
   `alembic upgrade head` inside `cloudcare-api/` if you migrate yourself.

### 5. Create the first admin

1. Render dashboard → `cloudcare-api` → **Shell** tab (available on paid
   plans; on free plans run it locally against the remote DB instead):
2. Locally:

```bash
cd cloudcare-api
DATABASE_URL="<paste the Internal Database URL from the Render Postgres page>" \
  python -m app.scripts.create_admin
# Full name: Ritu Malhotra
# Email: your-admin@yourdomain.com
# Password: (min 8 chars — use a password manager)
```

3. (Optional) seed the demo content so the dashboards look like the demo:

```bash
DATABASE_URL="<same internal URL>" python -m app.scripts.seed_demo
```

> Warning: `seed_demo --fresh` wipes ALL users — never run that against a
> database that has real accounts.

### 6. Check the API is healthy

- `https://your-api.onrender.com/health` → `{"status":"ok"}`
- `https://your-api.onrender.com/health/db` → `{"status":"ok","database":"connected"}`
- `https://your-api.onrender.com/docs` → should return **404** in production.

---

## Part 2 — Static frontend on Netlify

1. Go to <https://app.netlify.com> and sign in.
2. Click **Add new site** → **Import an existing project** → connect GitHub
   and pick the repo.
3. On the deploy settings page set:
   - **Base directory:** `cloudcare`
   - **Build command:** (leave empty)
   - **Publish directory:** `cloudcare` (or `.` once the base is set —
     `netlify.toml` in the repo root already sets `base = "cloudcare"`,
     so the UI fields can stay empty)
4. Click **Deploy site**.
5. When it finishes, note the URL, e.g. `https://random-name.netlify.app`.
   Rename it: **Site configuration → Change site name** → e.g. `cloudcare`.
6. Go back to Render (Part 1, step 3.4) and make sure `CORS_ORIGINS` includes
   `https://your-site.netlify.app`. Redeploy the API if you changed it.
7. **Edit the API URL in the frontend (two places):**
   - open `cloudcare/assets/js/config.js` and set:

```js
const PRODUCTION_API_URL = 'https://your-api.onrender.com'; // your Render URL
```

   - open `netlify.toml` and update the CSP `connect-src` entry
     `https://cloudcare-api.onrender.com` to the same URL (leave the
     `127.0.0.1:8000` / `localhost:8000` entries for local development).

   Commit and push; Netlify redeploys automatically.

### Free Render services sleep

Render's free plan spins the API down after ~15 min idle; the first request
after that takes ~30–60 s (the frontend shows a loading state meanwhile).
Upgrade the API to a paid plan for always-on.

---

## Part 3 — Post-deploy smoke-test checklist

Open two browser windows: a normal window and an incognito window.

| # | Test | Expected |
| --- | --- | --- |
| 1 | Visit `https://your-site.netlify.app/404-page-that-doesnt-exist` | Friendly 404 page |
| 2 | Register a new account (`/register.html`) | Toast "Account created", redirected to login |
| 3 | Log in with that account, role = Patient | Redirected to `dashboard-patient.html`, name in sidebar is yours |
| 4 | Try `dashboard-admin.html` directly from the patient window | Redirected to `dashboard-patient.html` (server role is authoritative) |
| 5 | Log in as admin (`dashboard-admin.html`) | Patients / Doctors / Appointments / Billing tables all show seeded rows |
| 6 | Log in as doctor `aakash.verma@cloudcare.demo` / `Demo!Pass123` (role: Doctor) | "My patients" shows only Dr. Verma's patients |
| 7 | From the doctor, open the browser console and run: <br>`fetch('<api>/api/admin/users',{headers:{Authorization:'Bearer '+localStorage.getItem('cloudcare_token')}}).then(r=>r.status)` | `403` |
| 8 | Patient window → Billing → Pay now on a pending invoice | "How to pay" modal with the clinic's payment instructions (no card form — edit `CC_PAYMENT_INSTRUCTIONS` in `config.js`) |
| 9 | Change password from Profile → Change password | Toast "Password updated"; this tab stays signed in, other devices are signed out on their next call |
| 10 | Wait 35 minutes, then click any dashboard action | Redirected to login with "session expired" toast |
| 11 | Log in with a wrong password 7 times quickly | Toast shows a rate-limit / generic error (slowapi kicks in) |
| 12 | `curl -s https://your-api.onrender.com/health/db` | `{"status":"ok","database":"connected"}` — never a stack trace |
| 13 | Check Render logs after a failed login | Error detail logged server-side only; response body is generic |

### Verifying security headers

```bash
curl -sI https://your-site.netlify.app | grep -i -E "content-security|x-frame|x-content"
curl -sI https://your-api.onrender.com/health | grep -i -E "x-frame|x-content"
```

Both should show the security headers.

### Verifying the audit trail

Log in as admin → console:

```js
ccApi('/api/admin/audit-logs?page_size=10').then(d => console.table(d.items));
```

You should see `login_success`, `register`, and `login_failure` rows from the
tests you just performed.
