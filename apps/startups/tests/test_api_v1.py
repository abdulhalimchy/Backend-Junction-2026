import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.startups import enrichment
from apps.startups.models import Startup

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:startups:startup-list")
LOOKUP_URL = reverse("v1:startups:startup-lookup")
SITE_FIELDS = {
    "name": "Northlight Robotics",
    "description": "Builds hull-inspection robots.",
    "headquarters": "Helsinki, Finland",
    "founded": "2023",
    "employees": "",
    "stage": "",
    "industry": "Marine robotics",
    "_other_names": [],
}


def detail_url(pk):
    return reverse("v1:startups:startup-detail", kwargs={"pk": pk})


@pytest.fixture
def website(monkeypatch, django_capture_on_commit_callbacks):
    """Website read stubbed; the background lookup runs inline when the request commits."""
    monkeypatch.setattr(enrichment, "read_website", lambda d: (dict(SITE_FIELDS), f"https://{d}/"))
    return django_capture_on_commit_callbacks


def test_manual_create_and_list(auth_client, user):
    res = auth_client.post(LIST_URL, {"name": "  Fjord AI ", "headquarters": "Oslo"}, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["name"] == "Fjord AI"
    assert res.json()["is_draft"] is False
    assert [s["name"] for s in auth_client.get(LIST_URL).json()["results"]] == ["Fjord AI"]


def test_manual_create_needs_a_name(auth_client):
    res = auth_client.post(LIST_URL, {"domain": "fjord.ai"}, format="json")
    assert res.status_code == 400


def test_lookup_fills_suggestions_from_website(auth_client, website):
    with website(execute=True):
        res = auth_client.post(LOOKUP_URL, {"domain": "https://www.Northlight.ai/about"}, format="json")
    assert res.status_code == 201, res.json()
    draft = auth_client.get(detail_url(res.json()["id"])).json()
    assert draft["domain"] == "northlight.ai"
    assert draft["is_draft"] is True
    assert draft["enrichment_status"] == "completed"
    assert draft["suggestions"]["name"]["value"] == "Northlight Robotics"
    assert draft["suggestions"]["headquarters"]["origin"] == "website"
    assert "employees" not in draft["suggestions"]  # never guessed
    assert auth_client.get(LIST_URL).json()["results"] == []  # drafts are not listed


def test_saving_draft_keeps_sources_only_for_unedited_values(auth_client, website):
    with website(execute=True):
        pk = auth_client.post(LOOKUP_URL, {"domain": "northlight.ai"}, format="json").json()["id"]
    reviewed = {"name": "Northlight Robotics", "headquarters": "Espoo, Finland", "founded": "2023"}
    res = auth_client.patch(detail_url(pk), {**reviewed, "is_draft": False}, format="json")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["is_draft"] is False
    assert set(body["sources"]) == {"name", "founded"}  # headquarters was edited, so it is the user's word


def test_lookup_rejects_bad_domain(auth_client):
    assert auth_client.post(LOOKUP_URL, {"domain": "not a domain"}, format="json").status_code == 400


def test_rename_and_delete(auth_client, user):
    s = Startup.objects.create(user=user, name="Old")
    assert auth_client.patch(detail_url(s.pk), {"name": "New"}, format="json").json()["name"] == "New"
    assert auth_client.delete(detail_url(s.pk)).status_code == 204
    assert not Startup.objects.exists()


def test_rename_cannot_blank_the_name(auth_client, user):
    s = Startup.objects.create(user=user, name="Old")
    assert auth_client.patch(detail_url(s.pk), {"name": " "}, format="json").status_code == 400


def test_other_users_startups_are_private(auth_client):
    other = Startup.objects.create(user=UserFactory(), name="Theirs")
    assert auth_client.get(detail_url(other.pk)).status_code == 404
    assert auth_client.delete(detail_url(other.pk)).status_code == 404
    assert auth_client.get(LIST_URL).json()["results"] == []


def test_domain_stem():
    assert enrichment.domain_stem("northlight.ai") == "northlight"
    assert enrichment.domain_stem("app.northlight.co.uk") == "northlight"


def analysis_url(startup, kind):
    return reverse("v1:startups:startup-analyses", kwargs={"pk": startup.pk, "kind": kind})


def test_analysis_never_runs_until_asked(auth_client, user, settings, monkeypatch):
    from apps.startups import analysis

    settings.CALA_API_KEY = "test"
    calls = []
    monkeypatch.setattr(
        analysis, "build_competitors", lambda s, c: calls.append("c") or {"companies": [{"name": "Rival"}]}
    )
    s = Startup.objects.create(user=user, name="Wolt", cala_company_id="abc")
    url = analysis_url(s, "competitors")

    assert auth_client.get(url).json()["status"] == "none"
    assert calls == []  # reading never starts a run

    res = auth_client.post(url)
    assert res.status_code == 202
    body = auth_client.get(url).json()
    assert body["status"] == "ready"
    assert body["data"]["companies"][0]["name"] == "Rival"
    assert body["finished_at"]
    auth_client.get(url)
    assert calls == ["c"]  # stored, not recomputed on read


def test_refresh_reruns_and_failure_keeps_previous_data(auth_client, user, settings, monkeypatch):
    from apps.startups import analysis

    settings.CALA_API_KEY = "test"
    s = Startup.objects.create(user=user, name="Wolt", industry="Delivery")
    url = analysis_url(s, "investors")
    monkeypatch.setattr(analysis, "build_investors", lambda s, c: {"investors": [{"name": "Accel"}]})
    auth_client.post(url)

    def boom(s, c):
        raise RuntimeError("Cala is down")

    monkeypatch.setattr(analysis, "build_investors", boom)
    auth_client.post(url)
    body = auth_client.get(url).json()
    assert body["status"] == "failed"
    assert body["error"] == "Cala is down"
    assert body["data"]["investors"][0]["name"] == "Accel"  # last good result is kept


def test_unknown_analysis_kind_is_404(auth_client, user):
    s = Startup.objects.create(user=user, name="X")
    assert auth_client.get(analysis_url(s, "nonsense")).status_code == 404


def test_match_score_parts():
    from collections import Counter

    from apps.startups.analysis import match

    row = {"backs": ["A", "B"], "last_deal": {"date": "2099-01-01", "basis": "round"}, "rounds_led": 3,
           "country": "Finland", "conflict": "competitor"}  # fmt: skip
    m = match(row, Counter({"seed": 4, "series-a": 1}), my_stage="seed", my_country="Finland", peer_count=5)
    points = {p["label"]: p["points"] for p in m["parts"]}
    assert points["Invests in your space"] == 27
    assert points["Invests at your stage"] == 25
    assert points["Leads rounds"] == 8
    assert points["Location"] == 10
    assert points["Backs a competitor"] == -10
    assert m["score"] == sum(points.values())


def test_country_and_stage_parsing():
    from apps.startups.analysis import country_of, stage_key

    assert country_of("Helsinki, Finland") == "Finland"
    assert country_of("Maarintie 6, 02150 ESPOO, FI-02150, FI") == "Finland"
    assert stage_key("PRE_SEED") == "pre-seed"
    assert stage_key("Series A") == "series-a"
    assert stage_key("SERIES_C") == "growth"


def test_deck_checks_are_scoped_to_their_startup(auth_client, user, tmp_path, settings):
    from apps.deck_checks.tests.factories import pdf_upload

    settings.MEDIA_ROOT = tmp_path
    settings.DECK_CHECK_ASYNC = True  # keep the pipeline off; only the listing matters here
    a = Startup.objects.create(user=user, name="7Scholar")
    b = Startup.objects.create(user=user, name="Discord")
    decks = reverse("v1:deck_checks:deck-check-list")
    auth_client.post(decks, {"file": pdf_upload(), "startup": str(a.pk)}, format="multipart")
    auth_client.post(decks, {"file": pdf_upload(), "startup": str(b.pk)}, format="multipart")
    auth_client.post(decks, {"file": pdf_upload()}, format="multipart")  # demo company: no startup

    def listed(q):
        return [d["startup"] for d in auth_client.get(decks, q).json()["results"]]

    assert listed({"startup": str(a.pk)}) == [str(a.pk)]
    assert listed({"startup": str(b.pk)}) == [str(b.pk)]
    assert listed({"startup": "none"}) == [None]
    assert len(listed({})) == 3


def test_cannot_attach_a_deck_to_someone_elses_startup(auth_client, tmp_path, settings):
    from apps.deck_checks.tests.factories import pdf_upload

    settings.MEDIA_ROOT = tmp_path
    theirs = Startup.objects.create(user=UserFactory(), name="Theirs")
    res = auth_client.post(
        reverse("v1:deck_checks:deck-check-list"),
        {"file": pdf_upload(), "startup": str(theirs.pk)},
        format="multipart",
    )
    assert res.status_code == 400


def test_deleting_a_startup_deletes_its_decks(
    auth_client, user, tmp_path, settings, django_capture_on_commit_callbacks
):
    from apps.deck_checks.models import DeckCheck
    from apps.deck_checks.tests.factories import pdf_upload

    settings.MEDIA_ROOT = tmp_path
    settings.DECK_CHECK_ASYNC = True
    s = Startup.objects.create(user=user, name="Gone")
    auth_client.post(
        reverse("v1:deck_checks:deck-check-list"),
        {"file": pdf_upload(), "startup": str(s.pk)},
        format="multipart",
    )
    path = DeckCheck.objects.get().file.path
    with django_capture_on_commit_callbacks(execute=True):
        assert auth_client.delete(detail_url(s.pk)).status_code == 204
    assert not DeckCheck.objects.exists()
    import os

    assert not os.path.exists(path)


SEARCH_URL = reverse("v1:startups:startup-search")
DISCORD = "d7b96d4a-0e4e-4e58-97c2-b454ca085a99"
ETF = "669aee5a-a41c-4ad6-8d16-39dea5b64810"


class FakeCala:
    """Two Cala companies named like "Discord": the company (website on record) and an ETF (none)."""

    ENTITIES = {
        ETF: {
            "id": ETF,
            "name": "ProShares Ultra Discord",
            "properties": {"description": {"value": "An ETF."}},
        },
        DISCORD: {
            "id": DISCORD,
            "name": "Discord",
            "properties": {
                "description": {"value": "A communication platform.", "sources": [{"name": "Crunchbase"}]},
                "website": {"value": "https://discord.com/"},
                "founding_date": {"value": "2015-05-13", "sources": [{"name": "Crunchbase"}]},
            },
        },
    }

    def search_entities(self, name, entity_types=None, limit=8):
        return [{"id": ETF, "name": "ProShares Ultra Discord"}, {"id": DISCORD, "name": "Discord"}]

    def retrieve_entity(self, entity_id, query=None):
        return self.ENTITIES[entity_id]


@pytest.fixture
def cala(monkeypatch, settings):
    settings.CALA_API_KEY = "test"
    monkeypatch.setattr(enrichment, "CalaClient", FakeCala)
    monkeypatch.setattr("apps.startups.enrichment.is_configured", lambda: True)


def test_search_by_name_puts_the_company_before_namesakes(auth_client, cala):
    res = auth_client.get(SEARCH_URL, {"q": "Discord"})
    assert res.status_code == 200, res.json()
    first, second = res.json()["results"]
    assert (first["id"], first["domain"], first["founded"]) == (DISCORD, "discord.com", "2015")
    assert (second["name"], second["domain"]) == ("ProShares Ultra Discord", "")


def test_search_needs_a_query(auth_client, cala):
    assert auth_client.get(SEARCH_URL, {"q": "d"}).status_code == 400


def test_lookup_by_picked_cala_company_uses_its_website(
    auth_client, cala, monkeypatch, django_capture_on_commit_callbacks
):
    read = []
    monkeypatch.setattr(enrichment, "read_website", lambda d: read.append(d))
    with django_capture_on_commit_callbacks(execute=True):
        res = auth_client.post(LOOKUP_URL, {"cala_id": DISCORD}, format="json")
    assert res.status_code == 201, res.json()
    draft = auth_client.get(detail_url(res.json()["id"])).json()
    assert read == ["discord.com"]  # the website on record is read, even with no domain typed
    assert draft["domain"] == "discord.com"
    assert draft["cala_company_id"] == DISCORD
    assert draft["suggestions"]["name"] == {"value": "Discord", "origin": "cala", "sources": []}
    assert draft["suggestions"]["founded"]["origin"] == "cala"


def test_lookup_needs_a_domain_or_a_company(auth_client):
    assert auth_client.post(LOOKUP_URL, {}, format="json").status_code == 400
