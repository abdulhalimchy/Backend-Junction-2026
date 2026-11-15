# Warmline Backend (Junction 2026)

Django + Django REST Framework API for Warmline. It uses SQLite for now; PostgreSQL is a `DATABASE_URL` change (see below).

## Stack

- Django 5.2 LTS, Django REST Framework
- JWT auth (`djangorestframework-simplejwt`) with refresh rotation and blacklisting
- OpenAPI docs (`drf-spectacular`) at `/api/docs/` and `/api/redoc/`
- `django-environ` for 12-factor config, `django-cors-headers` for the Next.js frontend
- pytest + factory-boy for tests, ruff for linting and formatting

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/development.txt
cp .env.example .env          # then set DJANGO_SECRET_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- API: http://localhost:8000/api/v1/
- Docs: http://localhost:8000/api/docs/
- Admin: http://localhost:8000/warmline-admin/

## Project layout

```
config/
  settings/
    base.py           shared settings, everything read from env vars
    development.py    DEBUG, browsable API, console email
    test.py           in-memory SQLite, fast hashing, no throttling
    production.py     PostgreSQL, WhiteNoise, HTTPS and HSTS hardening
  urls.py             mounts every app's versioned API
apps/
  core/               shared code with no tables of its own: base models, pagination,
                      error envelope, permissions, health check
  accounts/           custom User (email login, UUID pk), Profile, auth and "me" endpoints
    api/v1/           serializers.py, views.py, urls.py
    services.py       write-side business logic (views stay thin)
    signals.py        creates a Profile for every new User
    tests/
  events/             event spaces (Slush 2026) and the companies/investors attending
  alerts/             per-user notifications, with read/unread state
  deck_checks/        PDF pitch-deck uploads and per-claim verification results
  watchlist/          companies, investors and people a user watches (tags, notes)
requirements/         base.txt, development.txt, production.txt
```

## API versioning convention

Every app puts its API in `apps/<app>/api/v<N>/` with its own `serializers.py`, `views.py` and `urls.py` (`app_name = "<app>"`). `config/urls.py` collects them per version:

```python
api_v1_patterns = [
    path("", include("apps.core.api.v1.urls")),
    path("accounts/", include("apps.accounts.api.v1.urls")),
]
urlpatterns = [path("api/v1/", include((api_v1_patterns, "v1"), namespace="v1"))]
```

DRF `NamespaceVersioning` reads the namespace, so `request.version == "v1"`. URL names are `v1:<app>:<name>`, for example `reverse("v1:accounts:me")`.

**Adding a new app**

1. `python manage.py startapp <name> apps/<name>`, then set `name = "apps.<name>"` in its `apps.py`.
2. Add it to `LOCAL_APPS` in `config/settings/base.py`.
3. Create `apps/<name>/api/v1/{__init__,serializers,views,urls}.py`.
4. Add `path("<name>/", include("apps.<name>.api.v1.urls"))` to `api_v1_patterns`.
5. Inherit models from `apps.core.models.BaseModel` (UUID pk plus timestamps).

**Shipping v2**: create `api/v2/` in the apps that change, add `api_v2_patterns` mounted at `api/v2/` with namespace `"v2"`, and add `"v2"` to `REST_FRAMEWORK["ALLOWED_VERSIONS"]`. v1 keeps working unchanged.

## Endpoints (v1)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/health/` | public | Liveness and database check |
| POST | `/api/v1/accounts/auth/register/` | public | Create account, returns tokens and user |
| POST | `/api/v1/accounts/auth/login/` | public | Email and password, returns tokens and user |
| POST | `/api/v1/accounts/auth/refresh/` | public | Rotate refresh token, returns new access token |
| POST | `/api/v1/accounts/auth/logout/` | bearer | Blacklist a refresh token |
| GET, PATCH | `/api/v1/accounts/me/` | bearer | Current user (email is read-only) |
| GET, PATCH | `/api/v1/accounts/me/profile/` | bearer | Role, goals, Cala ids, privacy, `onboarding_completed` |
| POST | `/api/v1/accounts/me/change-password/` | bearer | Change password |
| GET | `/api/v1/events/` | bearer | Active events, with attendee counts and `new_this_week` |
| GET | `/api/v1/events/{slug}/` | bearer | One event, e.g. `slush-2026` |
| GET | `/api/v1/events/{slug}/attendees/` | bearer | Attending companies/investors (`?type=company\|investor`, `?q=`) |
| GET, POST | `/api/v1/events/{slug}/rsvps/` | bearer | Side events you are going to (`mine`) and anonymous counts per side event (`counts`); POST `{side_event_id}` to go |
| DELETE | `/api/v1/events/{slug}/rsvps/{side_event_id}/` | bearer | Not going any more |
| GET | `/api/v1/alerts/` | bearer | Your alerts (`?unread=true`, `?kind=`, `?event=`) |
| GET | `/api/v1/alerts/{id}/` | bearer | One alert |
| POST | `/api/v1/alerts/{id}/read/` | bearer | Mark one alert read |
| POST | `/api/v1/alerts/read-all/` | bearer | Mark all alerts read |
| GET | `/api/v1/alerts/unread-count/` | bearer | `{"unread": n}` for the badge |
| GET | `/api/v1/alerts/schedule/` | bearer | Nightly scan time and timezone, next run, last nightly run, your last manual check |
| POST | `/api/v1/alerts/scan/` | bearer | Check the companies and investors you watch for changes now; returns `{entities, alerts_created, skipped, errors}` (throttled, `THROTTLE_ALERT_SCAN`, 20/hour) |
| GET, POST | `/api/v1/deck-checks/` | bearer | List your deck checks, or upload a PDF (`multipart/form-data`) |
| GET, DELETE | `/api/v1/deck-checks/{id}/` | bearer | One deck check with its claims; delete also removes the file |
| GET, POST | `/api/v1/watchlist/` | bearer | List (`?type=`, `?tag=` or `none`, `?cala_id=`, `?q=`) or watch an entity; watching it again returns the existing item (200) |
| GET, PATCH, DELETE | `/api/v1/watchlist/{id}/` | bearer | One watch item; PATCH changes `name`, `tag`, `note` only |
| GET | `/api/v1/watchlist/summary/` | bearer | `{"total", "by_type", "by_tag"}` for the "Watching N" panel |

Users only ever see their own alerts, deck checks and watch items; anyone else's return 404. Events are read-only over the API and managed in the admin for now (later by a Cala sync job). Alerts are created by the backend, never by clients.

**Alert scan** (`apps/alerts/scan.py`). Alerts are created by the backend, from what Cala has on record for every company and investor on a watch list (plus each founder's own `cala_company_id`). One lookup per entity, however many people watch it; the facts are compared with the last `EntitySnapshot`:

- **Funding rounds** whose own date is within `ALERT_BACKFILL_DAYS` (60 by default), including on the first scan. Most round edges carry no date, so candidates are looked up (at most 5 per entity). Old rounds that appear in Cala for the first time are history, not alerts.
- **Investor moves**: a watched investor joined or led a recent round, or has a portfolio company new since the last scan. If the company is on the user's watch list, the reason says so ("Backs PortBotics: one of your competitors").
- **Event attendance** at an event space we host (matched on `Event.cala_event_id`; Slush 2026 is seeded).
- **Headcount**: the employee count range changed between two scans.

Each alert gets the watcher's `reason` (from their tag) and a `dedupe_key`, unique per user, so reruns never repeat an alert. Run it with `python manage.py scan_alerts` (`--user`, `--entity`, `--backfill-days`), from the admin (Watch items → "Scan selected entities"), or per user via `POST /alerts/scan/` (the "Check for changes" button). Demo fixture ids (not Cala UUIDs) are skipped. Each alert records the watched entity it is about (`entity_type`, `cala_id`; filter with `GET /alerts/?cala_id=`), and every run is recorded as a `ScanRun`.

**Nightly scan** (`apps/alerts/nightly.py`). Every watched entity is checked each night at `ALERT_SCAN_TIME` (default `02:00`) in `ALERT_SCAN_TIMEZONE` (default `Europe/Helsinki`). A missed night (server down) is caught up as soon as the scheduler runs again, and a failed run is retried after an hour. Ways to run it, all safe to combine:

- `python manage.py runserver`: in development the scheduler runs inside the dev server (`ALERT_SCHEDULER_IN_PROCESS`, on by default in development settings).
- `python manage.py run_alert_scheduler`: a long-running worker process (production; run exactly one).
- `python manage.py scan_alerts --nightly`: runs only if due, so cron or a hosted scheduler can call it hourly.

A newly watched entity is also checked right away in a background thread (`ALERT_SCAN_ON_WATCH`), so its recent rounds appear without waiting a night. `GET /api/v1/alerts/schedule/` returns the schedule, the last nightly run and your last manual check; watch list items carry `unread_alerts`, `last_checked_at` and `in_cala`.

Deck uploads are checked for size (`DECK_CHECK_MAX_UPLOAD_MB`, 20 by default), a `.pdf` name and real PDF content, and are stored under a random name. After the upload returns, a background thread (`apps/deck_checks/pipeline.py`) runs the check: `pending` → `processing` → `completed` or `failed` (poll `GET /deck-checks/{id}/`; `POST /deck-checks/{id}/retry/` re-runs a failed one).

1. The LLM (Gemini by default) reads the PDF natively (text, slide images, charts, so scanned decks work) and returns the factual claims with their slide number (`llm.extract_claims`).
2. The company is found in Cala (`cala_company_id`, else exact name match on `company_name`, else the name read from the deck) and its sourced facts are gathered as numbered evidence: founding date, HQ, headcount, funding rounds, investors, competitors (`evidence.py`).
3. The LLM gives each claim a verdict using only that evidence (`llm.verify_claims`). A verdict is only kept as `confirmed` or `differs` if it cites sourced evidence; everything else is `cant_verify` ("not on record", never "false"). The sources shown come from Cala, never from the model.

Needs `CALA_API_KEY` and `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY` with `DECK_CHECK_LLM_PROVIDER=anthropic`); set `DECK_CHECK_LLM_MODEL` to the exact model id. `DECK_CHECK_ASYNC=False` runs the check inline (tests, debugging). A thread is enough for one process; move `services.enqueue` to a task queue to scale.

Facts that come from Cala (`Event.sources`, `Alert.sources`, `DeckClaim.sources`, ...) are stored as JSON lists in the frontend's `Source` shape, defined once in `apps/core/serializers.py`.

Send `Authorization: Bearer <access>`. The register and login endpoints are rate-limited (`THROTTLE_AUTH`, 10/minute by default).

Errors always use the same shape:

```json
{ "error": { "status_code": 400, "code": "invalid", "message": "Invalid input.", "details": { "email": ["..."] } } }
```

## Switching to PostgreSQL

No code changes are needed:

```bash
pip install -r requirements/production.txt     # installs psycopg
# in .env:
DATABASE_URL=postgres://warmline:password@localhost:5432/warmline
python manage.py migrate
```

Keep it portable in the meantime: use `JSONField` rather than SQLite-only tricks, and don't hand-edit migrations in database-specific ways.

## Testing and linting

```bash
pytest
ruff check .
ruff format .
```

## Production with Docker

`Dockerfile` is a multi-stage build (dependencies in a builder stage; a slim, non-root runtime with static files
and bytecode built in). `compose.yaml` runs PostgreSQL, the API (gunicorn, threaded workers; migrations applied on
start), the nightly alert scheduler, and optionally Caddy with automatic HTTPS. Build on the server itself, so the
image matches its CPU (a Mac builds ARM images; most servers are x86).

On the server (Docker Engine with the Compose plugin):

```bash
git clone git@github.com:abdulhalimchy/Backend-Junction-2026.git warmline-backend && cd warmline-backend
cp .env.production.example .env
nano .env                     # DJANGO_SECRET_KEY, POSTGRES_PASSWORD, hosts/origins, API keys, DOMAIN
```

Then pick how HTTPS is handled:

```bash
# A) Caddy in compose gets the certificate (DNS for DOMAIN points here; ports 80 and 443 open)
docker compose --profile https up -d --build

# B) nginx on the server: the API listens on 127.0.0.1:8000. Site config, with the install and certbot
#    steps in its header: docker/nginx/warmline-api.conf
docker compose up -d --build
```

Day to day:

```bash
docker compose ps                                   # web should be "healthy"
docker compose logs -f web scheduler
docker compose exec web python manage.py createsuperuser
git pull && docker compose up -d --build            # deploy a new version (migrations run on start)
docker compose exec db pg_dump -U warmline warmline > backup.sql
```

Data lives in the `postgres_data` and `media` volumes, so containers can be rebuilt freely. Point the frontend's
`BACKEND_URL` at `https://<DOMAIN>`.

## Production (without Docker)

Set `DJANGO_SETTINGS_MODULE=config.settings.production`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DATABASE_URL`, `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`. Then run:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
gunicorn config.wsgi:application
```

### Deploy checklist

- [ ] Environment variables set (above), plus `CALA_API_KEY`, `GEMINI_API_KEY`, and `ALERT_SCAN_TIME` / `ALERT_SCAN_TIMEZONE` if 02:00 Helsinki time is not right.
- [ ] `python manage.py migrate` run against the production database.
- [ ] Web process running: `gunicorn config.wsgi:application`.
- [ ] **Nightly alert scan scheduled** (pick one; the web process does not run it in production):
  - **A worker process** that runs next to the web process and stays up:
    `python manage.py run_alert_scheduler`. Run exactly one. On Heroku, Railway or Render, add it as a worker /
    background worker, e.g. in a `Procfile`:

    ```
    web: gunicorn config.wsgi:application
    worker: python manage.py run_alert_scheduler
    ```

  - **A cron job** that calls `python manage.py scan_alerts --nightly`. It only scans when the night's run is due,
    so calling it hourly is safe and retries a failed night. On a server, `crontab -e`:

    ```
    0 * * * * cd /srv/warmline-backend && .venv/bin/python manage.py scan_alerts --nightly >> /var/log/warmline-alerts.log 2>&1
    ```

    On Render, Railway or Fly.io, create a cron job with schedule `0 * * * *` and the same command.
- [ ] After the first night, confirm a "Nightly schedule" run with status "Finished" in the admin (Alerts → Scan runs),
  or check `GET /api/v1/alerts/schedule/` (`last_nightly`).

Uploaded files are only served by Django in development. Before production, move media to private object storage (for example S3 via `django-storages`) and serve decks through short-lived signed URLs.
 