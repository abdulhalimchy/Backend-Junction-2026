"""
Docker HEALTHCHECK: GET /api/v1/health/ (checks the database too) on the local gunicorn.

Sends a Host from DJANGO_ALLOWED_HOSTS so Django accepts it, and X-Forwarded-Proto: https so the production
HTTPS redirect does not answer with a 301. Exit 0 healthy, 1 unhealthy. Standard library only.
"""

import os
import sys
import urllib.request

hosts = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]
host = next((h.lstrip(".") for h in hosts if h != "*"), "localhost")
port = os.environ.get("PORT", "8000")

request = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/v1/health/",
    headers={"Host": host, "X-Forwarded-Proto": "https"},
)
try:
    with urllib.request.urlopen(request, timeout=4) as response:  # noqa: S310 - fixed local URL
        sys.exit(0 if response.status == 200 else 1)
except Exception:  # noqa: BLE001 - any failure means unhealthy
    sys.exit(1)
