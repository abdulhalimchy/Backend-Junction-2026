import pytest
from django.urls import reverse

from apps.watchlist.models import WatchItem

from .factories import WatchItemFactory

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:watchlist:watch-list")
SUMMARY_URL = reverse("v1:watchlist:watch-summary")


def detail_url(item):
    return reverse("v1:watchlist:watch-detail", kwargs={"pk": item.pk})


def test_requires_auth(api_client):
    assert api_client.get(LIST_URL).status_code == 401


def test_watch_an_entity(auth_client, user):
    response = auth_client.post(
        LIST_URL,
        {"entity_type": "company", "cala_id": "portbotics", "name": "PortBotics", "tag": "competitor"},
    )

    assert response.status_code == 201, response.json()
    item = WatchItem.objects.get()
    assert (item.user, item.cala_id, item.tag, item.origin) == (user, "portbotics", "competitor", "manual")


def test_watching_twice_returns_the_existing_item(auth_client, user):
    existing = WatchItemFactory(user=user, cala_id="portbotics", tag="competitor")

    response = auth_client.post(
        LIST_URL, {"entity_type": "company", "cala_id": "portbotics", "tag": "partner"}
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(existing.id)
    assert response.json()["tag"] == "competitor"  # not overwritten
    assert WatchItem.objects.count() == 1


def test_same_id_can_be_watched_by_other_users(auth_client):
    WatchItemFactory(cala_id="portbotics")

    response = auth_client.post(LIST_URL, {"entity_type": "company", "cala_id": "portbotics"})

    assert response.status_code == 201


def test_invalid_type_and_tag_are_rejected(auth_client):
    response = auth_client.post(LIST_URL, {"entity_type": "planet", "cala_id": "x", "tag": "best-friend"})

    assert response.status_code == 400
    assert {"entity_type", "tag"} <= response.json()["error"]["details"].keys()


def test_list_is_private_and_filterable(auth_client, user):
    WatchItemFactory(user=user, name="PortBotics", tag="competitor")
    WatchItemFactory(
        user=user, name="Fjord Ventures", entity_type="investor", cala_id="fjord", tag="target_investor"
    )
    WatchItemFactory(user=user, name="Tidewise")
    WatchItemFactory(name="PortBotics")  # another user's item

    def names(**params):
        return sorted(i["name"] for i in auth_client.get(LIST_URL, params).json()["results"])

    assert names() == ["Fjord Ventures", "PortBotics", "Tidewise"]
    assert names(type="investor") == ["Fjord Ventures"]
    assert names(tag="competitor") == ["PortBotics"]
    assert names(tag="none") == ["Tidewise"]
    assert names(cala_id="fjord") == ["Fjord Ventures"]
    assert names(q="tide") == ["Tidewise"]


def test_update_tag_and_note_but_not_the_entity(auth_client, user):
    item = WatchItemFactory(user=user, cala_id="portbotics")

    response = auth_client.patch(
        detail_url(item), {"tag": "competitor", "note": "Closest match", "cala_id": "other"}
    )

    assert response.status_code == 200
    item.refresh_from_db()
    assert (item.tag, item.note, item.cala_id) == ("competitor", "Closest match", "portbotics")


def test_unwatch(auth_client, user):
    item = WatchItemFactory(user=user)

    assert auth_client.delete(detail_url(item)).status_code == 204
    assert not WatchItem.objects.exists()


def test_other_users_items_are_not_found(auth_client):
    item = WatchItemFactory()

    assert auth_client.get(detail_url(item)).status_code == 404
    assert auth_client.patch(detail_url(item), {"note": "x"}).status_code == 404
    assert auth_client.delete(detail_url(item)).status_code == 404


def test_summary_counts_own_items(auth_client, user):
    WatchItemFactory(user=user, tag="competitor")
    WatchItemFactory(user=user, tag="competitor")
    WatchItemFactory(user=user, entity_type="investor", tag="target_investor")
    WatchItemFactory(user=user)
    WatchItemFactory(tag="competitor")  # another user's item

    assert auth_client.get(SUMMARY_URL).json() == {
        "total": 4,
        "by_type": {"company": 3, "investor": 1},
        "by_tag": {"competitor": 2, "target_investor": 1},
    }
