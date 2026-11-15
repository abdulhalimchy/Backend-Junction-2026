from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.accounts.models import Profile, User


def _validate_password(password, user, *, field):
    """Run Django's password validators and report failures on the given field."""
    try:
        password_validation.validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({field: list(exc.messages)}) from exc


class ProfileSerializer(serializers.ModelSerializer):
    onboarding_completed = serializers.BooleanField(
        required=False, write_only=True, help_text="Set true to mark onboarding as finished."
    )
    is_onboarded = serializers.BooleanField(read_only=True)
    goals = serializers.ListField(child=serializers.CharField(max_length=120), required=False, max_length=20)

    class Meta:
        model = Profile
        fields = (
            "id",
            "role",
            "title",
            "goals",
            "cala_person_id",
            "cala_company_id",
            "cala_investor_id",
            "is_onboarded",
            "onboarding_completed",
            "onboarding_completed_at",
            "show_in_event_directory",
            "allow_alerts_email",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "onboarding_completed_at", "created_at", "updated_at")


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "full_name", "date_joined", "last_login", "profile")
        read_only_fields = ("id", "email", "date_joined", "last_login", "profile")


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, style={"input_type": "password"})
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=Profile.Role.choices, required=False, allow_blank=True, default="")

    def validate_email(self, value):
        value = User.objects.normalize_email(value).lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate(self, attrs):
        candidate = User(email=attrs["email"], full_name=attrs.get("full_name", ""))
        _validate_password(attrs["password"], candidate, field="password")
        return attrs


class LoginSerializer(TokenObtainPairSerializer):
    """Email + password -> access/refresh tokens, plus the user payload."""

    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


class AuthResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()


class GoogleSignInSerializer(serializers.Serializer):
    credential = serializers.CharField(
        help_text="The ID token from Google Identity Services (its `credential`)."
    )


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class DeleteAccountSerializer(serializers.Serializer):
    """Accounts with a password must send it. Google-only accounts have none, so they send nothing."""

    password = serializers.CharField(write_only=True, trim_whitespace=False, required=False, allow_blank=True)

    def validate(self, attrs):
        user = self.context["request"].user
        if user.has_usable_password() and not user.check_password(attrs.get("password", "")):
            raise serializers.ValidationError({"password": ["Password is incorrect."]})
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        _validate_password(attrs["new_password"], self.context["request"].user, field="new_password")
        return attrs
