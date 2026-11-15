#!/bin/sh
# Container entrypoint: optionally apply migrations, then run the command (gunicorn by default).
# Only the web service sets RUN_MIGRATIONS=1, so the scheduler never races it.
set -eu

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "Applying database migrations..."
    python manage.py migrate --noinput
fi

exec "$@"
