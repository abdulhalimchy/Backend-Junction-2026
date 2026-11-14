import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert

from .factories import AlertFactory

pytestmark = pytest.mark.django_db


def test_list_only_returns_own_alerts(auth_client, user):
    mine = AlertFactory(user=user)
    AlertFactory()  # someone else's

    response = auth_client.get(reverse("v1:alerts:alert-list"))

    assert response.status_code == 200
    assert [a["id"] for a in response.json()["results"]] == [str(mine.id)]


def test_cannot_read_someone_elses_alert(auth_client):
    other = AlertFactory()

    response = auth_client.get(reverse("v1:alerts:alert-detail", kwargs={"pk": other.pk}))

    assert response.status_code == 404


def test_filter_unread_and_kind(auth_client, user):
    AlertFactory(user=user, kind=Alert.Kind.PROFILE)
    target = AlertFactory(user=user, kind=Alert.Kind.INVESTOR_ACTIVITY)
    AlertFactory(user=user, kind=Alert.Kind.INVESTOR_ACTIVITY, read_at=timezone.now())

    response = auth_client.get(
        reverse("v1:alerts:alert-list"), {"unread": "true", "kind": "investor_activity"}
    )

    assert [a["id"] for a in response.json()["results"]] == [str(target.id)]


def test_mark_one_read_and_unread_count(auth_client, user):
    alert = AlertFactory(user=user)
    AlertFactory(user=user)

    response = auth_client.post(reverse("v1:alerts:alert-read", kwargs={"pk": alert.pk}))
    assert response.status_code == 200
    assert response.json()["is_read"] is True

    count = auth_client.get(reverse("v1:alerts:alert-unread-count")).json()
    assert count == {"unread": 1}


def test_read_all(auth_client, user):
    AlertFactory.create_batch(3, user=user)
    other = AlertFactory()

    response = auth_client.post(reverse("v1:alerts:alert-read-all"))

    assert response.json() == {"marked_read": 3}
    other.refresh_from_db()
    assert other.read_at is None
