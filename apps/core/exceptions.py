"""Uniform error envelope for every API response.

{
    "error": {
        "status_code": 400,
        "code": "invalid",
        "message": "Invalid input.",
        "details": {"email": ["This field is required."]}
    }
}
"""

from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import exceptions
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    # Translate Django's own exceptions into their DRF equivalents first.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        # Unhandled -> let Django return a 500 and log the traceback.
        return None

    if isinstance(exc, exceptions.ValidationError):
        code = "invalid"
        message = "Invalid input."
        details = response.data
    else:
        codes = exc.get_codes() if isinstance(exc, exceptions.APIException) else None
        code = codes if isinstance(codes, str) else "error"
        message = response.data.get("detail", str(exc)) if isinstance(response.data, dict) else str(exc)
        details = None

    response.data = {
        "error": {
            "status_code": response.status_code,
            "code": code,
            "message": str(message),
            "details": details,
        }
    }
    return response
