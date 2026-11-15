from django.contrib.auth.models import update_last_login
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts import services
from apps.accounts.models import User

from .serializers import (
    AuthResponseSerializer,
    ChangePasswordSerializer,
    DeleteAccountSerializer,
    GoogleSignInSerializer,
    LoginSerializer,
    LogoutSerializer,
    ProfileSerializer,
    RegisterSerializer,
    UserSerializer,
)


class ServiceUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Service unavailable."
    default_code = "unavailable"


class GoogleSignInFailed(APIException):
    # Not AuthenticationFailed: with no authentication classes DRF would turn that into a 403.
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Google sign-in failed."
    default_code = "google_sign_in_failed"


class AuthThrottleMixin:
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


@extend_schema(tags=["auth"])
class RegisterView(AuthThrottleMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=RegisterSerializer, responses={201: AuthResponseSerializer})
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.register_user(**serializer.validated_data)
        payload = {**services.issue_tokens(user), "user": UserSerializer(user).data}
        return Response(payload, status=status.HTTP_201_CREATED)


@extend_schema(tags=["auth"])
class GoogleSignInView(AuthThrottleMixin, APIView):
    """Signs in with a Google ID token, creating the account on first use. 201 when created, else 200."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=GoogleSignInSerializer, responses={200: AuthResponseSerializer, 201: AuthResponseSerializer}
    )
    def post(self, request):
        serializer = GoogleSignInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            claims = services.verify_google_credential(serializer.validated_data["credential"])
            user, created = services.sign_in_with_google(claims=claims)
        except services.GoogleSignInNotConfiguredError as exc:
            raise ServiceUnavailable(str(exc)) from exc
        except services.GoogleSignInError as exc:
            raise GoogleSignInFailed(str(exc)) from exc
        update_last_login(None, user)
        payload = {**services.issue_tokens(user), "user": UserSerializer(user).data}
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@extend_schema(tags=["auth"], responses={200: AuthResponseSerializer})
class LoginView(AuthThrottleMixin, TokenObtainPairView):
    serializer_class = LoginSerializer


@extend_schema(tags=["auth"])
class RefreshView(TokenRefreshView):
    pass


@extend_schema(tags=["auth"])
class LogoutView(APIView):
    """Blacklists the given refresh token. The short-lived access token simply expires."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=LogoutSerializer, responses={205: None})
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            if str(token["user_id"]) != str(request.user.pk):
                raise TokenError("Token does not belong to this user.")
            token.blacklist()
        except TokenError as exc:
            raise serializers.ValidationError({"refresh": [str(exc)]}) from exc
        return Response(status=status.HTTP_205_RESET_CONTENT)


@extend_schema(tags=["me"])
class MeView(generics.RetrieveUpdateAPIView):
    """The signed-in user's account."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return User.objects.select_related("profile").get(pk=self.request.user.pk)


@extend_schema(tags=["me"])
class MyProfileView(generics.RetrieveUpdateAPIView):
    """The signed-in user's profile: role, goals, Cala links, privacy and onboarding state."""

    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user.profile

    def perform_update(self, serializer):
        services.update_profile(profile=serializer.instance, data=dict(serializer.validated_data))


@extend_schema(tags=["me"])
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ChangePasswordSerializer, responses={204: None})
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        services.change_password(user=request.user, new_password=serializer.validated_data["new_password"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["me"])
class ExportDataView(APIView):
    """Everything stored about the signed-in user, as JSON (GDPR right of access and data portability)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: dict})
    def get(self, request):
        data = services.export_user_data(user=request.user)
        response = Response(data)
        response["Content-Disposition"] = 'attachment; filename="warmline-data.json"'
        return response


@extend_schema(tags=["me"])
class DeleteAccountView(APIView):
    """Permanently deletes the signed-in user and all their data. Needs the password; cannot be undone."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=DeleteAccountSerializer, responses={204: None})
    def post(self, request):
        serializer = DeleteAccountSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        services.delete_account(user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
