import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.portfolio import services
from apps.portfolio.models import PortfolioCompany
from apps.startups import enrichment
from apps.watchlist.models import WatchItem

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:portfolio:portfolio-company-list")
LOOKUP_URL = reverse("v1:portfolio:portfolio-company-lookup")
SUGGESTIONS_URL = reverse("v1:portfolio:portfolio-company-suggestions")
FUND_URL = reverse("v1:portfolio:portfolio-company-fund")
DISMISS_URL = reverse("v1:portfolio:portfolio-company-dismiss")

FUND_ID = "11111111-1111-4111-8111-111111111111"
ACME_ID = "22222222-2222-4222-8222-222222222222"
BETA_ID = "33333333-3333-4333-8333-333333333333"

SUGGESTIONS = {
    "name": {"value": "Acme Robotics", "origin": "cala", "sources": [{"name": "Cala"}]},
    "headquarters": {"value": "Oslo, Norway", "origin": "cala", "sources": [{"name": "Registry"}]},
    "industry": {"value": "Robotics", "origin": "website", "sources": [{"name": "acme.ai"}]},
}


def detail_url(pk):
    return reverse("v1:portfolio:portfolio-company-detail", kwargs={"pk": pk})


@pytest.fixture
def lookup(monkeypatch, django_capture_on_commit_callbacks):
    """The startups lookup stubbed; the background run happens inline when the request commits."""
    monkeypatch.setattr(
        enrichment,
        "enrich",
        lambda domain, cala_id="": (dict(SUGGESTIONS), cala_id or ACME_ID, "Found.", domain or "acme.ai"),
    )
    return django_capture_on_commit_callbacks


class FakeCala:
    def retrieve_entity(self, entity_id, query=None):
        assert entity_id == FUND_ID
        edge = lambda i, n, d: {  # noqa: E731
            "id": i,
            "name": n,
            "properties": {"sources": [{"name": "Press", "date": d, "source_rank": "PRESS"}]},
        }
        return {
            "id": FUND_ID,
            "name": "Fjord Ventures",
            "relationships": {
                "outgoing": {
                    "INVESTED_IN": [edge(ACME_ID, "Acme", "2024-03-01"), edge(BETA_ID, "Beta", "2025-01-10")]
                }
            },
        }


@pytest.fixture
def cala(monkeypatch, settings):
    settings.CALA_API_KEY = "test"
    monkeypatch.setattr(services, "CalaClient", FakeCala)


def test_manual_create_with_deal_terms(auth_client):
    body = {
        "name": " Fjord AI ",
        "invested_amount": "250000",
        "currency": "eur",
        "ownership": "4.5",
        "board_seat": True,
    }
    res = auth_client.post(LIST_URL, body, format="json")
    assert res.status_code == 201, res.json()
    data = res.json()
    assert (data["name"], data["currency"], data["ownership"], data["status"]) == (
        "Fjord AI",
        "EUR",
        "4.50",
        "active",
    )
    assert [c["name"] for c in auth_client.get(LIST_URL).json()["results"]] == ["Fjord AI"]


def test_validation(auth_client):
    assert auth_client.post(LIST_URL, {"domain": "x.ai"}, format="json").status_code == 400  # no name
    assert auth_client.post(LIST_URL, {"name": "A", "ownership": "120"}, format="json").status_code == 400
    assert auth_client.post(LIST_URL, {"name": "A", "invested_on": "March"}, format="json").status_code == 400


def test_lookup_draft_then_save_keeps_sources_for_unedited(auth_client, lookup, user):
    with lookup(execute=True):
        res = auth_client.post(LOOKUP_URL, {"domain": "acme.ai"}, format="json")
    assert res.status_code == 201, res.json()
    pk = res.json()["id"]
    draft = auth_client.get(detail_url(pk)).json()
    assert draft["is_draft"] is True and draft["enrichment_status"] == "completed"
    assert auth_client.get(LIST_URL).json()["results"] == []

    saved = auth_client.patch(
        detail_url(pk),
        {
            "name": "Acme Robotics",
            "headquarters": "Bergen, Norway",
            "invested_amount": "1000000",
            "is_draft": False,
        },
        format="json",
    ).json()
    assert saved["is_draft"] is False
    assert set(saved["sources"]) == {"name"}  # headquarters was edited
    assert not WatchItem.objects.filter(user=user).exists()  # the watch list holds only what the user adds


def test_quick_add_fills_empty_fields(auth_client, lookup, user):
    with lookup(execute=True):
        res = auth_client.post(LOOKUP_URL, {"cala_id": ACME_ID, "name": "Acme", "quick": True}, format="json")
    assert res.status_code == 201, res.json()
    c = PortfolioCompany.objects.get(pk=res.json()["id"])
    assert not c.is_draft and c.origin == "cala"
    assert c.name == "Acme"  # kept as given
    assert c.headquarters == "Oslo, Norway" and "headquarters" in c.sources
    assert not WatchItem.objects.filter(user=user).exists()
    # Adding it again is refused.
    again = auth_client.post(LOOKUP_URL, {"cala_id": ACME_ID, "name": "Acme", "quick": True}, format="json")
    assert again.status_code == 409


def test_suggestions_exclude_added_and_dismissed(auth_client, cala, user):
    assert auth_client.get(SUGGESTIONS_URL).json() == {"fund": None, "results": []}
    res = auth_client.put(FUND_URL, {"cala_id": FUND_ID}, format="json")
    assert res.status_code == 200, res.json()
    assert res.json()["fund"]["name"] == "Fjord Ventures"
    assert [r["name"] for r in res.json()["results"]] == ["Beta", "Acme"]  # newest report first

    PortfolioCompany.objects.create(user=user, name="Acme", cala_company_id=ACME_ID)
    assert (
        auth_client.post(DISMISS_URL, {"cala_id": BETA_ID, "name": "Beta"}, format="json").status_code == 204
    )
    assert auth_client.get(SUGGESTIONS_URL).json()["results"] == []

    assert auth_client.put(FUND_URL, {"cala_id": None}, format="json").json()["fund"] is None


def test_delete_leaves_the_watch_list_alone(auth_client, user):
    c = PortfolioCompany.objects.create(user=user, name="Acme", cala_company_id=ACME_ID)
    WatchItem.objects.create(user=user, entity_type="company", cala_id=ACME_ID, tag="pipeline")
    assert auth_client.delete(detail_url(c.pk)).status_code == 204
    assert WatchItem.objects.filter(user=user, cala_id=ACME_ID).exists()


def test_private_to_owner(auth_client):
    theirs = PortfolioCompany.objects.create(user=UserFactory(), name="Secret")
    assert auth_client.get(detail_url(theirs.pk)).status_code == 404
    assert auth_client.get(LIST_URL).json()["results"] == []
