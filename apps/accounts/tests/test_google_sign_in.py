import pytest
from django.urls import reverse

from apps.accounts.models import User

from .factories import DEFAULT_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db

URL = reverse("v1:accounts:google")
CLAIMS = {"email": "Anna@Example.com", "email_verified": True, "name": "Anna Virtanen", "sub": "123"}


@pytest.fixture(autouse=True)
def client_id(settings):
    settings.GOOGLE_CLIENT_ID = "test-client.apps.googleusercontent.com"


@pytest.fixture(autouse=True)
def no_throttle_history():
    # The view keeps its "auth" scope throttle in tests; earlier auth tests must not use up its budget.
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def google(monkeypatch):
    """Stands in for Google's token check: records the call and returns `google.claims` (or raises)."""

    class Fake:
        claims = dict(CLAIMS)
        error = None
        calls = []

    def verify(token, request, audience):
        Fake.calls.append((token, audience))
        if Fake.error:
            raise Fake.error
        return Fake.claims

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", verify)
    return Fake


def test_first_sign_in_creates_a_passwordless_account(api_client, google):
    res = api_client.post(URL, {"credential": "tok"})

    assert res.status_code == 201, res.json()
    body = res.json()
    assert {"access", "refresh", "user"} <= body.keys()
    assert body["user"]["email"] == "anna@example.com"
    assert body["user"]["full_name"] == "Anna Virtanen"
    user = User.objects.get(email="anna@example.com")
    assert not user.has_usable_password()
    assert user.last_login is not None
    assert google.calls == [("tok", "test-client.apps.googleusercontent.com")]


def test_next_sign_in_returns_the_same_account(api_client, google):
    first = api_client.post(URL, {"credential": "tok"}).json()["user"]["id"]
    res = api_client.post(URL, {"credential": "tok"})

    assert res.status_code == 200
    assert res.json()["user"]["id"] == first
    assert User.objects.count() == 1


def test_matches_an_existing_email_account_and_keeps_its_password(api_client, google):
    user = UserFactory(email="anna@example.com", full_name="")

    res = api_client.post(URL, {"credential": "tok"})

    assert res.status_code == 200
    assert res.json()["user"]["id"] == str(user.id)
    user.refresh_from_db()
    assert user.check_password(DEFAULT_PASSWORD)
    assert user.full_name == "Anna Virtanen"


def test_unverified_email_is_refused(api_client, google):
    google.claims = {**CLAIMS, "email_verified": False}

    assert api_client.post(URL, {"credential": "tok"}).status_code == 401
    assert not User.objects.exists()


def test_invalid_token_is_refused(api_client, google):
    google.error = ValueError("Token expired")

    res = api_client.post(URL, {"credential": "tok"})

    assert res.status_code == 401
    assert res.json()["error"]["message"] == "Google sign-in failed. Try again."


def test_disabled_account_is_refused(api_client, google):
    UserFactory(email="anna@example.com", is_active=False)

    assert api_client.post(URL, {"credential": "tok"}).status_code == 401


def test_missing_credential_is_invalid(api_client, google):
    assert api_client.post(URL, {}).status_code == 400


def test_unconfigured_client_id_answers_503(api_client, google, settings):
    settings.GOOGLE_CLIENT_ID = ""

    assert api_client.post(URL, {"credential": "tok"}).status_code == 503
    assert google.calls == []


def test_google_account_can_delete_itself_without_a_password(api_client, google):
    access = api_client.post(URL, {"credential": "tok"}).json()["access"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    res = api_client.post(reverse("v1:accounts:delete-account"), {})

    assert res.status_code == 204
    assert not User.objects.exists()


def test_password_account_still_needs_its_password_to_delete(auth_client, user):
    res = auth_client.post(reverse("v1:accounts:delete-account"), {})

    assert res.status_code == 400
    assert User.objects.filter(pk=user.pk).exists()


def test_same_google_account_can_sign_in_again_after_deleting(api_client, google):
    first = api_client.post(URL, {"credential": "tok"}).json()
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {first['access']}")
    assert api_client.post(reverse("v1:accounts:delete-account"), {}).status_code == 204
    api_client.credentials()

    # The old session is gone for good...
    assert api_client.post(reverse("v1:accounts:refresh"), {"refresh": first["refresh"]}).status_code == 401
    # ...and the same Google account gets a new, empty account.
    res = api_client.post(URL, {"credential": "tok"})
    assert res.status_code == 201
    assert res.json()["user"]["id"] != first["user"]["id"]
    assert res.json()["user"]["profile"]["is_onboarded"] is False
