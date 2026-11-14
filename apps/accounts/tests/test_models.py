import pytest

from apps.accounts.models import Profile, User


@pytest.mark.django_db
class TestUserModel:
    def test_create_user_normalises_email_and_creates_profile(self):
        user = User.objects.create_user(email="Anna@Example.COM", password="x")

        assert user.email == "anna@example.com"
        assert user.check_password("x")
        assert not user.is_staff
        assert Profile.objects.filter(user=user).exists()

    def test_create_superuser(self):
        admin = User.objects.create_superuser(email="admin@example.com", password="x")

        assert admin.is_staff and admin.is_superuser
        assert admin.profile is not None

    def test_create_user_requires_email(self):
        with pytest.raises(ValueError):
            User.objects.create_user(email="", password="x")

    def test_login_lookup_is_case_insensitive(self, user):
        assert User.objects.get_by_natural_key(user.email.upper()) == user
