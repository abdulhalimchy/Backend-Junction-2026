import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.markets import analysis, company
from apps.markets.models import MarketAnalysis

pytestmark = pytest.mark.django_db

FIND_URL = reverse("v1:markets:company-find")
OPEN_URL = reverse("v1:markets:company-open")
ENTITY = "11111111-2222-3333-4444-555555555555"
SRC = {
    "name": "Reg",
    "document": "https://example.com",
    "date": "",
    "date_precision": None,
    "source_rank": None,
}


@pytest.fixture
def fake(monkeypatch, settings):
    settings.CALA_API_KEY = "test"
    cache.clear()
    calls = {"n": 0, "plan": None}

    def read_text(text):
        is_company = "orange" in text.lower()
        return {
            "kind": "company" if is_company else "not_company",
            "reason": "",
            "suggestions": [],
            **company.place_fields(
                {"name": "Orange", "place": "Europe", "countries": ["France"]} if is_company else {}, ""
            ),
        }

    monkeypatch.setattr(company, "read_text", read_text)
    monkeypatch.setattr(
        company,
        "find_candidates",
        lambda client, name: [
            {"id": ENTITY, "name": name, "entity_type": "Company", "description": "Telecom", "exact": True}
        ],
    )

    def section(name, value):
        def fn(client, plan):
            calls["n"] += 1
            calls["plan"] = plan
            return value

        return fn

    profile = {
        "id": ENTITY,
        "name": "Orange",
        "description": {"value": "Telecom operator", "sources": [SRC]},
        "rounds": [],
        "investors": [],
    }
    monkeypatch.setattr(
        company,
        "SECTIONS",
        {
            "profile": section("profile", profile),
            "competitors": section("competitors", {"definition": "q", "companies": [], "unresolved": 0}),
            "news": section("news", {"question": "q", "summary": "", "evidence": []}),
        },
    )
    monkeypatch.setattr(
        "apps.markets.summary.summarise_company",
        lambda name, place, year, facts: {"headline": {"text": f"{name}: {len(facts)} facts", "sources": []}},
    )
    return calls


def test_find_reads_text_then_lists_candidates(auth_client, fake):
    body = auth_client.post(FIND_URL, {"text": "orange telecom in europe"}, format="json").json()
    assert body["verdict"]["kind"] == "company" and body["verdict"]["name"] == "Orange"
    assert body["candidates"][0]["id"] == ENTITY


def test_find_non_company_spends_nothing(auth_client, fake):
    body = auth_client.post(FIND_URL, {"text": "fix my code"}, format="json").json()
    assert body["candidates"] == [] and body["verdict"]["kind"] == "not_company"


def test_open_runs_company_sections_and_stores_history(auth_client, fake):
    payload = {
        "entity_id": ENTITY,
        "name": "Orange",
        "description": "Telecom",
        "text": "orange telecom in europe",
    }
    a = auth_client.post(OPEN_URL, payload, format="json").json()
    assert a["kind"] == "company" and a["status"] == "ready" and a["place"] == "Europe"
    assert a["data"]["profile"]["name"] == "Orange"
    assert a["data"]["summary"]["headline"]["text"] == "Orange: 1 facts"
    assert fake["n"] == 3
    assert fake["plan"]["competitors_query"].startswith("companies.competitors_of=Orange.location=Europe")
    assert all("Telecom" in q for q in fake["plan"]["news_questions"])

    # Reopening reads the stored result; the history lists it with its kind.
    auth_client.post(OPEN_URL, payload, format="json")
    assert fake["n"] == 3
    history = auth_client.get(reverse("v1:markets:analyses")).json()
    assert [(h["kind"], h["sector"]) for h in history] == [("company", "Orange")]


def test_stop_company_first_run_removes_it(auth_client, fake, monkeypatch, settings, user):
    settings.DECK_CHECK_ASYNC = True
    monkeypatch.setattr(analysis, "_run_in_thread", lambda *a: None)
    a = auth_client.post(OPEN_URL, {"entity_id": ENTITY, "name": "Orange"}, format="json").json()
    assert a["status"] == "loading"
    assert auth_client.post(reverse("v1:markets:analysis-cancel", args=[a["id"]])).json()["deleted"] is True
    assert not MarketAnalysis.objects.filter(user=user).exists()


def test_company_without_record_fails(auth_client, fake, monkeypatch):
    def boom(client, plan):
        raise RuntimeError("not found")

    monkeypatch.setitem(company.SECTIONS, "profile", boom)
    a = auth_client.post(OPEN_URL, {"entity_id": ENTITY, "name": "Orange"}, format="json").json()
    assert a["status"] == "failed" and "not found" in a["error"]
