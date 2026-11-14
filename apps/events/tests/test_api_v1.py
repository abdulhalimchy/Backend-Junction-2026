import datetime

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.events.models import EventAttendance, SideEventRSVP

from .factories import EventAttendanceFactory, EventFactory

pytestmark = pytest.mark.django_db


def test_events_require_authentication(api_client):
    assert api_client.get(reverse("v1:events:event-list")).status_code == 401


def test_event_detail_includes_attendee_stats(auth_client):
    event = EventFactory(slug="slush-2026")
    EventAttendanceFactory.create_batch(2, event=event)
    EventAttendanceFactory(
        event=event,
        entity_type=EventAttendance.EntityType.INVESTOR,
        joined_on=datetime.date.today() - datetime.timedelta(days=30),
    )

    response = auth_client.get(reverse("v1:events:event-detail", kwargs={"slug": "slush-2026"}))

    assert response.status_code == 200
    body = response.json()
    assert body["attendee_companies"] == 2
    assert body["attendee_investors"] == 1
    assert body["new_this_week"] == 2


def test_attendees_can_be_filtered_by_type(auth_client):
    event = EventFactory()
    EventAttendanceFactory(event=event, name="Northlight Robotics")
    EventAttendanceFactory(
        event=event, entity_type=EventAttendance.EntityType.INVESTOR, name="Fjord Ventures"
    )

    url = reverse("v1:events:event-attendees", kwargs={"slug": event.slug})
    response = auth_client.get(url, {"type": "investor"})

    assert response.status_code == 200
    assert [a["name"] for a in response.json()["results"]] == ["Fjord Ventures"]


def test_inactive_events_are_hidden(auth_client):
    EventFactory(slug="old", is_active=False)

    response = auth_client.get(reverse("v1:events:event-detail", kwargs={"slug": "old"}))

    assert response.status_code == 404


def rsvps_url(event):
    return reverse("v1:events:event-rsvps", kwargs={"slug": event.slug})


def test_rsvp_add_list_and_remove(auth_client):
    event = EventFactory()

    first = auth_client.post(rsvps_url(event), {"side_event_id": "nordic-vc-breakfast"}, format="json")
    again = auth_client.post(rsvps_url(event), {"side_event_id": "nordic-vc-breakfast"}, format="json")

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json() == {"mine": ["nordic-vc-breakfast"], "counts": {"nordic-vc-breakfast": 1}}

    url = reverse(
        "v1:events:event-rsvp-delete",
        kwargs={"slug": event.slug, "side_event_id": "nordic-vc-breakfast"},
    )
    assert auth_client.delete(url).status_code == 204
    assert auth_client.get(rsvps_url(event)).json() == {"mine": [], "counts": {}}


def test_rsvp_counts_are_anonymous_and_mine_is_per_user(auth_client):
    event = EventFactory()
    other = UserFactory()
    SideEventRSVP.objects.create(user=other, event=event, side_event_id="climate-dinner")

    body = auth_client.get(rsvps_url(event)).json()

    assert body == {"mine": [], "counts": {"climate-dinner": 1}}


def test_rsvp_rejects_bad_ids(auth_client):
    event = EventFactory()

    response = auth_client.post(rsvps_url(event), {"side_event_id": "not a slug!"}, format="json")

    assert response.status_code == 400
