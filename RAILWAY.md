# Railway backend deployment

This service hosts the Django JSON API only. React stays on its frontend host. No Redis or filesystem volume is needed for the backend.

## Source and service

The current Git repository is inside `frontend/`, so it does not contain this backend. Publish the contents of `backend/` in a separate private repository, or include backend in a parent repository. Do not upload `.env`, virtual environments, local database or `LOCAL_ADMIN.txt`.

For a backend-only repository use root `/` and config path `/railway.json`. For a parent repository use service Root Directory `/backend` and Config File Path `/backend/railway.json`.

Connect that repository to a Railway service. Add a PostgreSQL service in the same Railway project/environment. Dockerfile builds Python 3.12 with Gunicorn; the server listens on Railway's PORT. Migrations run as a pre-deploy command, before traffic switches. Health check `/api/health/` also checks PostgreSQL.

## Service variables

- `DATABASE_URL=${{Postgres.DATABASE_URL}}` (replace Postgres with your database service name).
- `DJANGO_DEBUG=0`
- `DJANGO_SECRET_KEY`: a unique random secret, at least 50 characters. Generate locally with `python -c "import secrets; print(secrets.token_urlsafe(64))"` and paste into Railway Variables.
- `DJANGO_ALLOWED_HOSTS`: comma-separated backend domain and any frontend host preserved by your reverse proxy. No schemes or paths. Railway's generated public domain and health-check host are added automatically on Railway.
- `DJANGO_CSRF_TRUSTED_ORIGINS`: exact frontend HTTPS origin, e.g. `https://your-cafe.vercel.app`, plus your custom frontend domain if used. No trailing slash.
- Optional `WEB_CONCURRENCY=2` (default); use 1 for a very small instance.

Generate a public domain in the backend service's Networking settings. Do not set DEBUG=1 in production. Production refuses a missing/short/placeholder secret. PostgreSQL is required. Keep the same secret across redeployments.

## First deployment

Once migrations finish, use Railway's service SSH session to run:

```sh
python manage.py seed_menu
python manage.py createsuperuser
```

These run inside the deployed service with its private PostgreSQL connection. There is no Django admin website; use these credentials on the React `/admin` page. Seeding imports the original 48 items and preserves existing edits. It does not transfer the current local menu edits, accounts or orders. For an existing database migration, back up and restore PostgreSQL separately before opening ordering; do not seed over an intended data import.

No default production password is included. Change password in React Settings after login. Never reset admin passwords automatically on each deploy.

## Frontend connection (required)

Frontend requests use relative `/api/` URLs and same-origin session cookies. Keep that contract: proxy `/api/:path*` from the frontend host to `https://YOUR-BACKEND.up.railway.app/api/:path*`. On Vercel, add this before the SPA fallback in `vercel.json` after replacing the hostname:

```json
{
  "rewrites": [
    {"source": "/api/:path(.*)", "destination": "https://YOUR-BACKEND.up.railway.app/api/:path*"},
    {"source": "/(.*)", "destination": "/index.html"}
  ]
}
```

Use HTTPS on both hosts. Do not point frontend fetch directly at a different domain without redesigning cookies/CORS. The current localhost Vite proxy stays unchanged. The actual deployed backend domain is needed before updating the frontend rewrite.

## Verify

- Backend `/api/health/` returns HTTP 200 with `{"status":"ok"}`.
- Frontend `/api/session/` returns JSON, not index.html.
- Login, save settings, place a test COD order, check polling and receipt, then cancel that test order.
- Back up PostgreSQL before subsequent schema changes. Railway health checks gate deployments, not continuous uptime monitoring.
- Current IP rate limits use the immediate peer address. Behind proxies this can share limits among visitors. Verify your deployed proxy's trusted client-IP contract before changing this; do not blindly trust user-supplied X-Forwarded-For.

References: https://docs.railway.com/guides/django , https://docs.railway.com/deployments/pre-deploy-command , https://docs.railway.com/deployments/healthchecks

Production checks enable HTTPS redirect, secure cookies, frame protection and one-year HSTS. HSTS subdomain coverage and preload remain off until you control and verify every subdomain; Django check --deploy reports W005/W021 for these intentional choices.
