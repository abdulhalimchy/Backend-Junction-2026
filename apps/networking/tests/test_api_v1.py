import pytest
from django.urls import reverse

from apps.events.tests.factories import EventFactory
from apps.networking.models import MetEntry

from .factories import MetEntryFactory

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:networking:met-list")


def detail_url(entry):
    return reverse("v1:networking:met-detail", kwargs={"pk": entry.pk})


def test_create_entry_for_current_user_linked_to_event(auth_client, user):
    EventFactory(slug="slush-2026")

    response = auth_client.post(
        LIST_URL,
        {
            "cala_person_id": "jonas-berg",
            "person_name": "Jonas Berg",
            "met_on": "2026-11-12",
            "where": "Slush pre-event, Helsinki",
            "note": "Asked for deck after Slush.",
            "event": "slush-2026",
        },
    )

    assert response.status_code == 201, response.json()
    entry = MetEntry.objects.get()
    assert entry.user == user
    assert entry.event.slug == "slush-2026"


def test_unknown_event_is_rejected(auth_client):
    response = auth_client.post(LIST_URL, {"cala_person_id": "x", "met_on": "2026-11-12", "event": "nope"})

    assert response.status_code == 400
    assert "event" in response.json()["error"]["details"]


def test_list_is_private_and_searchable(auth_client, user):
    MetEntryFactory(user=user, person_name="Elin Strand", note="Mostly follows")
    MetEntryFactory(user=user, person_name="Pekka Harju")
    MetEntryFactory(person_name="Elin Strand")  # another user's entry

    response = auth_client.get(LIST_URL, {"q": "elin"})

    assert response.status_code == 200
    assert [e["person_name"] for e in response.json()["results"]] == ["Elin Strand"]


def test_update_and_delete_own_entry(auth_client, user):
    entry = MetEntryFactory(user=user)

    assert auth_client.patch(detail_url(entry), {"note": "Follow up"}).json()["note"] == "Follow up"
    assert auth_client.delete(detail_url(entry)).status_code == 204
    assert not MetEntry.objects.exists()


def test_cannot_touch_someone_elses_entry(auth_client):
    entry = MetEntryFactory()

    assert auth_client.patch(detail_url(entry), {"note": "hijack"}).status_code == 404
    assert auth_client.delete(detail_url(entry)).status_code == 404
