# Warmline Backend (Junction 2026)

Django + Django REST Framework API for Warmline: sourced company data for founders and investors, built on
Cala's knowledge graph. It signs users in with Google, stores what they add (startups, portfolio, decks, watch
list), runs the long Cala and LLM work in the background, and is the only place that holds the Cala and LLM keys.
The Next.js frontend is in `../Frontend-Junction-2026`.

SQLite in development; PostgreSQL in production (a `DATABASE_URL` change, see below).

## Stack

- Python 3.12+, Django 5.2 LTS, Django REST Framework
- Google sign-in (`google-auth` verifies the ID token) issuing JWTs (`djangorestframework-simplejwt`, refresh
  rotation and blacklisting)
- Cala knowledge graph over `httpx` (`apps/core/cala.py`), Gemini (`google-genai`) or Anthropic for the LLM steps
- OpenAPI docs (`drf-spectacular`) at `/api/docs/` and `/api/redoc/`
- `django-environ` for config, `django-cors-headers` for the frontend
- pytest + factory-boy for tests, ruff for linting and formatting
- Docker: gunicorn (threaded workers), PostgreSQL, optional Caddy for HTTPS

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/development.txt
cp .env.example .env          # then set DJANGO_SECRET_KEY, CALA_API_KEY, GEMINI_API_KEY, GOOGLE_CLIENT_ID, CALA_GATEWAY_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- API: http://localhost:8000/api/v1/
- Docs: http://localhost:8000/api/docs/
- Admin: http://localhost:8000/warmline-admin/

Run `python manage.py migrate` again after pulling changes that add migrations.

## Environment variables

`.env.example` (development) and `.env.production.example` (Docker) list them with comments. The ones to set:

| Variable | Needed for |
|---|---|
| `DJANGO_SECRET_KEY` | Always |
| `GOOGLE_CLIENT_ID` | Sign-in. The OAuth web client ID; same value as `NEXT_PUBLIC_GOOGLE_CLIENT_ID` in the frontend |
| `CALA_API_KEY` | Every Cala lookup (gateway, analyses, deck checks, alert scan) |
| `CALA_GATEWAY_KEY` | The frontend's shared Cala lookups (sent as `X-Gateway-Key`). Same value in the frontend; any long random string |
| `GEMINI_API_KEY` | Deck checks and market analysis (or `ANTHROPIC_API_KEY` with `DECK_CHECK_LLM_PROVIDER=anthropic` for deck checks) |
| `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` | The frontend's origin |
| `DATABASE_URL` | PostgreSQL in production |

Optional, with defaults: `JWT_ACCESS_MINUTES` (15), `JWT_REFRESH_DAYS` (7), `DECK_CHECK_MAX_UPLOAD_MB` (20),
`DECK_CHECK_LLM_MODEL`, `DECK_CHECK_REUSE_HOURS` (24), `DECK_CHECK_WEB_SEARCH` (on), `MARKET_LLM_MODEL`,
`ALERT_SCAN_TIME` (02:00), `ALERT_SCAN_TIMEZONE` (Europe/Helsinki), `ALERT_BACKFILL_DAYS` (60),
`ALERT_SCHEDULER_IN_PROCESS`, `ALERT_SCAN_ON_WATCH`, `API_DOCS_ENABLED`, and rate limits `THROTTLE_ANON`,
`THROTTLE_USER`, `THROTTLE_AUTH`, `THROTTLE_ALERT_SCAN`, `THROTTLE_CALA`.

## Project layout

```
config/
  settings/
    base.py           shared settings, everything read from env vars
    development.py    DEBUG, browsable API, console email, alert scheduler in runserver
    test.py           in-memory SQLite, fast hashing, no throttling, work runs inline
    production.py     PostgreSQL, WhiteNoise, HTTPS and HSTS hardening
  urls.py             mounts every app's versioned API, the docs and the admin
apps/
  core/               shared code: base models, pagination, error envelope, permissions, health check,
                      the Cala client (cala.py) and the Cala gateway for the frontend (api/v1/cala.py)
  accounts/           custom User (email, UUID pk), Profile (role, goals), Google sign-in, data export, deletion
  startups/           a founder's startups: lookup from Cala and the website, competitors and investors analyses
  portfolio/          an investor's portfolio: fund suggestions, lookups, funding/competitor/investor analyses
  markets/            market and company analyses (Cala + LLM), the market guard and report summaries
  deck_checks/        PDF pitch-deck uploads and per-claim verification against Cala
  watchlist/          companies and investors a user follows (tags, notes)
  alerts/             changes found on watched entities, the alert scan and its nightly scheduler
  events/             event spaces (Slush 2026), attendees and side-event RSVPs
docker/               gunicorn config, entrypoint, healthcheck, Caddyfile, nginx site config
requirements/         base.txt, development.txt, production.txt
```

Each app keeps its API in `api/v1/` (serializers, views, urls), write-side logic in `services.py`, and tests in
`tests/`.

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

## Authentication

Sign-in is Google only from the frontend:

1. The frontend gets an ID token from Google and sends it to `POST /api/v1/accounts/auth/google/`
   (`{"credential": "..."}`).
2. The backend verifies it against `GOOGLE_CLIENT_ID` (signature, expiry, issuer, audience) and requires a
   verified email. The account is matched on email, or created without a password on first sign-in (201; 200
   for a returning user).
3. It returns `access` and `refresh` JWTs plus the user and profile (`role`, `is_onboarded`).

Send `Authorization: Bearer <access>` on every other request. `POST /accounts/auth/refresh/` rotates the refresh
token; `POST /accounts/auth/logout/` blacklists it. The email/password endpoints (`register`, `login`,
`change-password`) still exist for the admin and tests. Sign-in endpoints are rate-limited (`THROTTLE_AUTH`,
10/minute).

**Cala gateway.** The frontend never calls Cala directly: `/api/v1/cala/<path>` forwards five read-only Cala
endpoints with the server's key. It accepts a signed-in user, or the frontend server's shared
`CALA_GATEWAY_KEY` in `X-Gateway-Key` (its cached lookups are shared across users). Cala's "not found" comes
back as 404 (`code: not_found`); other Cala failures as 502/402/429 with a `code`.

**Your data (GDPR).** `GET /accounts/me/export/` returns everything stored about the user as JSON (access and
portability). `POST /accounts/me/delete/` deletes the user and everything they own (every user table cascades),
removes their uploaded PDFs and blacklists their sessions; Google accounts need no password, password accounts
send `{"password": "..."}`. The same Google account can sign up again afterwards and starts empty.

## Endpoints (v1)

All under `/api/v1/`. Everything needs a bearer token unless marked public.

| Method | Path | Purpose |
|---|---|---|
| GET | `health/` | Liveness and database check (public) |
| **Accounts** | | |
| POST | `accounts/auth/google/` | Sign in with a Google ID token (public) |
| POST | `accounts/auth/register/`, `accounts/auth/login/` | Email and password (public; admin and tests) |
| POST | `accounts/auth/refresh/` | Rotate the refresh token (public) |
| POST | `accounts/auth/logout/` | Blacklist a refresh token |
| GET, PATCH | `accounts/me/` | Current user |
| GET, PATCH | `accounts/me/profile/` | Role, goals, Cala ids, privacy, `onboarding_completed` |
| GET | `accounts/me/export/` | All of the user's data as JSON |
| POST | `accounts/me/delete/` | Delete the account and everything in it |
| POST | `accounts/me/change-password/` | Change password |
| **Cala** | | |
| GET, POST | `cala/<path>` | Gateway to Cala's entity search, entity lookup, introspection, knowledge search and query |
| **Startups** (founders) | | |
| GET, POST | `startups/` | List, or add by hand |
| GET, PATCH, DELETE | `startups/{id}/` | One startup; PATCH a draft with `is_draft: false` to save it |
| GET | `startups/search/?q=` | Companies in Cala named like `q` |
| POST | `startups/lookup/` | Start from a domain or a Cala company; poll the draft until `enrichment_status` is done |
| GET, POST | `startups/{id}/analyses/{kind}/` | `competitors` or `investors`: read, or run/refresh in the background |
| **Portfolio** (investors) | | |
| GET, POST | `portfolio/` | List, or add by hand |
| GET, PUT, PATCH, DELETE | `portfolio/{id}/` | One portfolio company |
| PUT | `portfolio/fund/` | Link the fund (a Cala id), or unlink with null |
| GET | `portfolio/suggestions/` | Companies Cala links to the fund |
| POST | `portfolio/dismiss/` | Hide a suggestion |
| POST | `portfolio/lookup/` | Start from a domain or a Cala company (`quick: true` adds a suggestion in one click) |
| GET | `portfolio/analyses/` | Every stored analysis |
| POST | `portfolio/analyses/refresh/` | Run analyses in the background (`kinds`, `company`) |
| GET | `portfolio/analyses/status/` | Cheap to poll: runs in progress and a `version` |
| **Markets** | | |
| GET, POST, DELETE | `markets/analyses/` | History; POST `{text, force}` opens a market analysis; DELETE clears history |
| GET, DELETE | `markets/analyses/{id}/` | One analysis (poll while `status` is `loading`) |
| POST | `markets/analyses/{id}/refresh/`, `markets/analyses/{id}/cancel/` | Re-run or stop it |
| POST | `markets/companies/find/` | Free text to Cala company candidates |
| POST | `markets/companies/open/` | Open the analysis of a picked company |
| POST | `markets/guard/` | Is this text a market, a company or neither (LLM) |
| POST | `markets/summary/` | Executive summary from numbered Cala facts, every claim checked |
| **Deck checks** | | |
| GET, POST | `deck-checks/` | List, or upload a PDF (`multipart/form-data`) |
| GET, DELETE | `deck-checks/{id}/` | One check with its claims; delete also removes the file |
| POST | `deck-checks/{id}/retry/` | Re-run a failed check |
| **Watch list and alerts** | | |
| GET, POST | `watchlist/` | List (`?type=`, `?tag=`, `?cala_id=`, `?q=`) or watch an entity |
| GET, PATCH, DELETE | `watchlist/{id}/` | One item; PATCH changes `name`, `tag`, `note` |
| GET | `watchlist/summary/` | Totals by type and tag |
| GET | `alerts/` | Your alerts (`?unread=true`, `?kind=`, `?event=`, `?cala_id=`) |
| GET | `alerts/{id}/` | One alert |
| POST | `alerts/{id}/read/`, `alerts/read-all/` | Mark read |
| GET | `alerts/unread-count/` | `{"unread": n}` |
| GET | `alerts/schedule/` | Nightly scan time, next run, last runs |
| POST | `alerts/scan/` | Check your watched entities now (throttled, `THROTTLE_ALERT_SCAN`, 20/hour) |
| **Events** | | |
| GET | `events/`, `events/{slug}/` | Event spaces, e.g. `slush-2026` |
| GET | `events/{slug}/attendees/` | Attending companies and investors (`?type=`, `?q=`) |
| GET, POST | `events/{slug}/rsvps/` | Side-event RSVPs (the frontend no longer shows them) |
| DELETE | `events/{slug}/rsvps/{side_event_id}/` | Remove an RSVP |

Users only ever see their own startups, portfolio, analyses, deck checks, watch items and alerts; anyone else's
return 404. Events are read-only over the API and managed in the admin.

Errors always use the same shape:

```json
{ "error": { "status_code": 400, "code": "invalid", "message": "Invalid input.", "details": { "email": ["..."] } } }
```

## Background work

Cala and LLM calls can take a minute, so the long work runs in background threads after the request returns,
and the frontend polls: startup and portfolio lookups (`enrichment_status`), startup and portfolio analyses,
market and company analyses (`status`), deck checks, and the alert scan for a newly watched entity. A run left
`loading` by a restarted server is treated as dead (analyses after 5-6 minutes, deck checks after
`DECK_CHECK_STALE_MINUTES`, 20) and can be run again. Tests and `DECK_CHECK_ASYNC=False` run the work
inline. Threads live in the web process, which is why gunicorn never recycles workers (`docker/gunicorn.conf.py`);
to scale out, move the `enqueue` functions to a task queue.

Still synchronous: the Cala gateway (knowledge search can take 45-60 seconds), `alerts/scan/`, and the market
guard and summary LLM calls. gunicorn allows 180 seconds and the nginx config 200.

**Alert scan** (`apps/alerts/scan.py`). Alerts are created by the backend, from what Cala has on record for every company and investor on a watch list (plus each founder's own `cala_company_id`). One lookup per entity, however many people watch it; the facts are compared with the last `EntitySnapshot`:

- **Funding rounds** whose own date is within `ALERT_BACKFILL_DAYS` (60 by default), including on the first scan. Most round edges carry no date, so candidates are looked up (at most 5 per entity). Old rounds that appear in Cala for the first time are history, not alerts.
- **Investor moves**: a watched investor joined or led a recent round, or has a portfolio company new since the last scan. If the company is on the user's watch list, the reason says so ("Backs PortBotics: one of your competitors").
- **Event attendance** at an event space we host (matched on `Event.cala_event_id`; Slush 2026 is seeded).
- **Headcount**: the employee count range changed between two scans.

Each alert gets the watcher's `reason` (from their tag) and a `dedupe_key`, unique per user, so reruns never repeat an alert. Run it with `python manage.py scan_alerts` (`--user`, `--entity`, `--backfill-days`), from the admin (Watch items → "Scan selected entities"), or per user via `POST /alerts/scan/` (the "Check for changes" button). Each alert records the watched entity it is about (`entity_type`, `cala_id`; filter with `GET /alerts/?cala_id=`), and every run is recorded as a `ScanRun`.

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

Facts that come from Cala (`Event.sources`, `Alert.sources`, `DeckClaim.sources`, ...) are stored as JSON lists in
the frontend's `Source` shape, defined once in `apps/core/serializers.py`.

## Management commands

```bash
python manage.py scan_alerts [--user --entity --backfill-days --nightly]   # run the alert scan
python manage.py run_alert_scheduler                                         # nightly scheduler worker
python manage.py eval_decks [--runs --no-search]                             # score deck checks against labelled decks (evals/decks)
```

## Testing and linting

```bash
pytest
ruff check .
ruff format .
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

- [ ] Environment variables set (above), plus `CALA_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_CLIENT_ID`, `CALA_GATEWAY_KEY` (same value as the
  frontend's), and `ALERT_SCAN_TIME` / `ALERT_SCAN_TIMEZONE` if 02:00 Helsinki time is not right.
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
