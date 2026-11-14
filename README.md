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
- Admin: http://localhost:8000/admin/

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
  networking/         the user's private "met" list
  deck_checks/        PDF pitch-deck uploads and per-claim verification results
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
| GET | `/api/v1/alerts/` | bearer | Your alerts (`?unread=true`, `?kind=`, `?event=`) |
| GET | `/api/v1/alerts/{id}/` | bearer | One alert |
| POST | `/api/v1/alerts/{id}/read/` | bearer | Mark one alert read |
| POST | `/api/v1/alerts/read-all/` | bearer | Mark all alerts read |
| GET | `/api/v1/alerts/unread-count/` | bearer | `{"unread": n}` for the badge |
| GET, POST | `/api/v1/networking/met/` | bearer | List (`?event=`, `?person=`, `?q=`) or add people you met |
| GET, PATCH, DELETE | `/api/v1/networking/met/{id}/` | bearer | One met entry |
| GET, POST | `/api/v1/deck-checks/` | bearer | List your deck checks, or upload a PDF (`multipart/form-data`) |
| GET, DELETE | `/api/v1/deck-checks/{id}/` | bearer | One deck check with its claims; delete also removes the file |

Users only ever see their own alerts, met entries and deck checks; anyone else's return 404. Events are read-only over the API and managed in the admin for now (later by a Cala sync job). Alerts are created by the backend, never by clients.

Deck uploads are checked for size (`DECK_CHECK_MAX_UPLOAD_MB`, 20 by default), a `.pdf` name and real PDF content, and are stored under a random name. A new deck check stays `pending`: extracting claims and verifying them against Cala isn't built yet (`apps/deck_checks/services.py` marks where it plugs in).

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

## Production

Set `DJANGO_SETTINGS_MODULE=config.settings.production`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DATABASE_URL`, `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`. Then run:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
gunicorn config.wsgi:application
```

Uploaded files are only served by Django in development. Before production, move media to private object storage (for example S3 via `django-storages`) and serve decks through short-lived signed URLs.
