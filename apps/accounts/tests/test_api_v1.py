import os

import pytest
from django.urls import reverse

from apps.accounts.models import User

from .factories import DEFAULT_PASSWORD

pytestmark = pytest.mark.django_db


class TestRegister:
    url = reverse("v1:accounts:register")

    def test_register_returns_tokens_and_user(self, api_client):
        response = api_client.post(
            self.url,
            {
                "email": "New@Example.com",
                "password": DEFAULT_PASSWORD,
                "full_name": "Anna V",
                "role": "founder",
            },
        )

        assert response.status_code == 201, response.json()
        body = response.json()
        assert {"access", "refresh", "user"} <= body.keys()
        assert body["user"]["email"] == "new@example.com"
        assert body["user"]["profile"]["role"] == "founder"

    def test_register_rejects_duplicate_email_case_insensitively(self, api_client, user):
        response = api_client.post(self.url, {"email": user.email.upper(), "password": DEFAULT_PASSWORD})

        assert response.status_code == 400
        assert "email" in response.json()["error"]["details"]

    def test_register_rejects_weak_password_on_password_field(self, api_client):
        response = api_client.post(self.url, {"email": "a@example.com", "password": "123"})

        assert response.status_code == 400
        assert "password" in response.json()["error"]["details"]
        assert not User.objects.filter(email="a@example.com").exists()


class TestLoginRefreshLogout:
    def test_login_with_email_and_password(self, api_client, user):
        response = api_client.post(
            reverse("v1:accounts:login"), {"email": user.email, "password": DEFAULT_PASSWORD}
        )

        assert response.status_code == 200
        assert response.json()["user"]["id"] == str(user.id)

    def test_login_with_wrong_password_fails(self, api_client, user):
        response = api_client.post(reverse("v1:accounts:login"), {"email": user.email, "password": "nope"})

        assert response.status_code == 401
        assert response.json()["error"]["status_code"] == 401

    def test_refresh_then_logout_blacklists_token(self, api_client, user):
        tokens = api_client.post(
            reverse("v1:accounts:login"), {"email": user.email, "password": DEFAULT_PASSWORD}
        ).json()

        refreshed = api_client.post(reverse("v1:accounts:refresh"), {"refresh": tokens["refresh"]})
        assert refreshed.status_code == 200
        new_refresh = refreshed.json()["refresh"]

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refreshed.json()['access']}")
        assert api_client.post(reverse("v1:accounts:logout"), {"refresh": new_refresh}).status_code == 205

        again = api_client.post(reverse("v1:accounts:refresh"), {"refresh": new_refresh})
        assert again.status_code == 401


class TestMe:
    def test_requires_authentication(self, api_client):
        assert api_client.get(reverse("v1:accounts:me")).status_code == 401

    def test_get_and_patch_me(self, auth_client, user):
        response = auth_client.patch(reverse("v1:accounts:me"), {"full_name": "Jonas Berg", "email": "x@y.z"})

        assert response.status_code == 200
        assert response.json()["full_name"] == "Jonas Berg"
        user.refresh_from_db()
        assert user.email != "x@y.z"  # email is read-only here

    def test_patch_profile_and_complete_onboarding(self, auth_client):
        url = reverse("v1:accounts:me-profile")
        response = auth_client.patch(
            url,
            {"role": "investor", "goals": ["meet founders"], "onboarding_completed": True},
            format="json",
        )

        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["role"] == "investor"
        assert body["goals"] == ["meet founders"]
        assert body["is_onboarded"] is True
        assert body["onboarding_completed_at"] is not None

    def test_change_password(self, auth_client, user):
        response = auth_client.post(
            reverse("v1:accounts:change-password"),
            {"current_password": DEFAULT_PASSWORD, "new_password": "An0ther-str0ng-one"},
        )

        assert response.status_code == 204
        user.refresh_from_db()
        assert user.check_password("An0ther-str0ng-one")

    def test_change_password_rejects_wrong_current(self, auth_client):
        response = auth_client.post(
            reverse("v1:accounts:change-password"),
            {"current_password": "wrong", "new_password": "An0ther-str0ng-one"},
        )

        assert response.status_code == 400
        assert "current_password" in response.json()["error"]["details"]


class TestDeleteAccount:
    url = reverse("v1:accounts:delete-account")

    @pytest.fixture(autouse=True)
    def media_root(self, tmp_path, settings):
        settings.MEDIA_ROOT = tmp_path

    def test_requires_authentication(self, api_client):
        assert api_client.post(self.url, {"password": DEFAULT_PASSWORD}).status_code == 401

    def test_wrong_password_deletes_nothing(self, auth_client, user):
        response = auth_client.post(self.url, {"password": "wrong"})

        assert response.status_code == 400
        assert "password" in response.json()["error"]["details"]
        assert User.objects.filter(pk=user.pk).exists()

    def test_deletes_the_user_their_data_and_their_files(self, auth_client, user):
        from apps.alerts.models import Alert
        from apps.alerts.tests.factories import AlertFactory
        from apps.deck_checks.models import DeckCheck, DeckClaim
        from apps.deck_checks.tests.factories import DeckCheckFactory, DeckClaimFactory
        from apps.watchlist.models import WatchItem
        from apps.watchlist.tests.factories import WatchItemFactory

        deck = DeckCheckFactory(user=user)
        DeckClaimFactory(deck_check=deck)
        WatchItemFactory(user=user)
        AlertFactory(user=user)
        other = DeckCheckFactory()  # another user's data stays
        path = deck.file.path

        response = auth_client.post(self.url, {"password": DEFAULT_PASSWORD})

        assert response.status_code == 204
        assert not User.objects.filter(pk=user.pk).exists()
        assert not DeckCheck.objects.filter(user_id=user.pk).exists()
        assert not DeckClaim.objects.filter(deck_check_id=deck.pk).exists()
        assert not WatchItem.objects.filter(user_id=user.pk).exists()
        assert not Alert.objects.filter(user_id=user.pk).exists()
        assert not os.path.exists(path)
        assert DeckCheck.objects.filter(pk=other.pk).exists()
        assert os.path.exists(other.file.path)

    def test_refresh_tokens_stop_working(self, api_client, user):
        tokens = api_client.post(
            reverse("v1:accounts:login"), {"email": user.email, "password": DEFAULT_PASSWORD}
        ).json()
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        assert api_client.post(self.url, {"password": DEFAULT_PASSWORD}).status_code == 204

        api_client.credentials()
        assert (
            api_client.post(reverse("v1:accounts:refresh"), {"refresh": tokens["refresh"]}).status_code == 401
        )


class TestExportData:
    url = reverse("v1:accounts:export-data")

    def test_requires_authentication(self, api_client):
        assert api_client.get(self.url).status_code == 401

    def test_exports_the_account_and_everything_it_owns_but_not_others(
        self, auth_client, user, tmp_path, settings
    ):
        from apps.deck_checks.tests.factories import DeckCheckFactory, DeckClaimFactory
        from apps.watchlist.tests.factories import WatchItemFactory

        settings.MEDIA_ROOT = tmp_path
        user.profile.role = "investor"
        user.profile.goals = ["Source new deals"]
        user.profile.save()
        deck = DeckCheckFactory(user=user)
        DeckClaimFactory(deck_check=deck)
        WatchItemFactory(user=user)
        WatchItemFactory()  # someone else's

        response = auth_client.get(self.url)

        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]
        body = response.json()
        assert body["account"]["email"] == user.email
        assert "password" not in body["account"]
        assert body["accounts.profile"][0]["role"] == "investor"
        assert body["accounts.profile"][0]["goals"] == ["Source new deals"]
        assert len(body["watchlist.watchitem"]) == 1
        [exported_deck] = body["deck_checks.deckcheck"]
        assert exported_deck["file"] == deck.file.name
        assert len(exported_deck["deck_checks.deckclaim"]) == 1
        assert not any(k.startswith("token_blacklist") for k in body)
