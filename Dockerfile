# syntax=docker/dockerfile:1.7
#
# Warmline backend: Django API served by gunicorn. Two stages:
#   builder  installs the production dependencies into a virtualenv (pip cache kept between builds)
#   runtime  a clean slim image with only that virtualenv and the code, running as a non-root user
#
# Build:  docker build -t warmline-backend .
# Run:    see compose.yaml (PostgreSQL, the alert scheduler and an optional Caddy for HTTPS).

ARG PYTHON_VERSION=3.12

# ---- builder ---------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Only the requirement files, so code changes do not reinstall dependencies.
COPY requirements/base.txt requirements/production.txt /tmp/requirements/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install -r /tmp/requirements/production.txt

# ---- runtime ---------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PORT=8000

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY . .

# Static files are built into the image (WhiteNoise serves them); bytecode is compiled once here.
# Dummy values: collectstatic needs settings to load, not a real database or secret.
RUN DJANGO_SECRET_KEY=collectstatic-only \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATABASE_URL=sqlite:////tmp/collectstatic.sqlite3 \
    python manage.py collectstatic --noinput \
    && python -m compileall -q apps config \
    && rm -f /tmp/collectstatic.sqlite3 \
    && mkdir -p /app/media \
    && chown app:app /app/media \
    && chmod +x /app/docker/entrypoint.sh

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--config", "/app/docker/gunicorn.conf.py"]
