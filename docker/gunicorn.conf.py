"""Gunicorn settings for the container. Every value can be overridden with an environment variable."""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# Threaded workers: requests wait on Cala and the LLM (network I/O), and analyses and deck checks run in
# background threads inside the worker that started them.
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Some requests call the LLM synchronously (e.g. planning a market search), which can take a minute or more.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "180"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = 5

# No max_requests: recycling a worker would kill the analyses running in its background threads.

# Heartbeat files in memory, not on the container's overlay filesystem.
worker_tmp_dir = "/dev/shm"  # noqa: S108 - gunicorn's documented setting for containers

# Behind a reverse proxy on the Docker network: trust its X-Forwarded-* headers.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
