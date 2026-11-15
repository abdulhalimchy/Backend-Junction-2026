import hmac

from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsOwner(BasePermission):
    """Object-level: allow access only to objects owned by the requesting user.

    The object must expose a `user` attribute (or be the user itself).
    """

    def has_object_permission(self, request, view, obj):
        owner = getattr(obj, "user", obj)
        return owner == request.user


class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


class IsAuthenticatedOrGatewayKey(BasePermission):
    """A signed-in user, or the frontend server sending the shared CALA_GATEWAY_KEY as X-Gateway-Key."""

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        key = settings.CALA_GATEWAY_KEY
        sent = request.headers.get("X-Gateway-Key", "")
        return bool(key) and hmac.compare_digest(sent.encode(), key.encode())
