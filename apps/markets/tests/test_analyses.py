import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.markets import analysis
from apps.markets.models import MarketAnalysis

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:markets:analyses")
SRC = {
    "name": "Grand View",
    "document": "https://example.com/r",
    "date": "",
    "date_precision": None,
    "source_rank": None,
}


def section(text):
    return lambda client, sector, region, year: {
        "question": f"{sector}?",
        "summary": text,
        "evidence": [{"id": "k1", "text": text, "sources": [SRC]}],
    }


@pytest.fixture
def fake_cala(monkeypatch, settings):
    settings.CALA_API_KEY = "test"
    cache.clear()
    calls = {"n": 0}
    players = {
        "companies": [{"name": "Acme", "description": None, "hq": None, "rounds": [], "investors": []}]
    }

    def count(fn):
        def wrapped(*a):
            calls["n"] += 1
            return fn(*a)

        return wrapped

    monkeypatch.setattr(
        analysis,
        "SECTIONS",
        {
            "size": count(section("Valued at USD 10 billion in 2024.")),
            "deals": count(section("Acme raised EUR 5 million.")),
            "trends": count(section("AI is a trend.")),
            "players": count(lambda *a: {"definition": "q", "unresolved": 0, **players}),
        },
    )
    monkeypatch.setattr(
        "apps.markets.summary.summarise",
        lambda market, region, year, facts: {
            "headline": {"text": f"{market}: {len(facts)} facts", "sources": []}
        },
    )
    monkeypatch.setattr(
        "apps.markets.guard.check_market_text",
        lambda text, known: {
            "kind": "market",
            "reason": "",
            "suggestions": [],
            "canonical": "edtech",
            "tokens": {},
        },
    )
    return {"calls": calls, "players": players}


def test_requires_auth(api_client):
    assert api_client.get(LIST_URL).status_code == 401


def test_open_runs_once_and_stores_history(auth_client, fake_cala):
    res = auth_client.post(LIST_URL, {"text": "Ed Tech", "region": "europe"}, format="json")
    assert res.status_code == 200
    a = res.json()["analysis"]
    assert a["sector"] == "edtech" and a["status"] == "ready"
    assert a["data"]["summary"]["headline"]["text"] == "edtech: 4 facts"
    assert a["data"]["players"]["companies"][0]["name"] == "Acme"
    assert fake_cala["calls"]["n"] == 4

    # Opening it again reads the stored result: no new Cala calls.
    auth_client.post(LIST_URL, {"text": "edtech", "region": "europe"}, format="json")
    assert fake_cala["calls"]["n"] == 4
    history = auth_client.get(LIST_URL).json()
    assert [h["sector"] for h in history] == ["edtech"]
    assert history[0]["headline"] == "edtech: 4 facts"


def test_refresh_updates_and_records_changes(auth_client, fake_cala):
    a = auth_client.post(LIST_URL, {"text": "edtech", "region": "europe"}, format="json").json()["analysis"]
    fake_cala["players"]["companies"] = [
        {"name": "Beta", "description": None, "hq": None, "rounds": [], "investors": []}
    ]
    res = auth_client.post(reverse("v1:markets:analysis-refresh", args=[a["id"]]))
    assert res.status_code == 202
    fresh = auth_client.get(reverse("v1:markets:analysis", args=[a["id"]])).json()
    assert fresh["runs"] == 2
    assert fresh["data"]["players"]["companies"][0]["name"] == "Beta"
    assert "New leading players: Beta." in fresh["changes"]
    assert "No longer listed: Acme." in fresh["changes"]


def test_delete_one_and_clear_all(auth_client, fake_cala, user):
    a = auth_client.post(LIST_URL, {"text": "edtech", "region": "europe"}, format="json").json()["analysis"]
    auth_client.post(LIST_URL, {"text": "fintech", "region": "nordics", "force": True}, format="json")
    assert MarketAnalysis.objects.filter(user=user).count() == 2

    assert auth_client.delete(reverse("v1:markets:analysis", args=[a["id"]])).status_code == 204
    assert MarketAnalysis.objects.filter(user=user).count() == 1

    assert auth_client.delete(LIST_URL).json()["deleted"] == 1
    assert not MarketAnalysis.objects.filter(user=user).exists()


def test_non_market_text_spends_nothing(auth_client, fake_cala, monkeypatch):
    monkeypatch.setattr(
        "apps.markets.guard.check_market_text",
        lambda text, known: {
            "kind": "not_market",
            "reason": "A task.",
            "suggestions": [],
            "canonical": "",
            "tokens": {},
        },
    )
    body = auth_client.post(LIST_URL, {"text": "fix my code", "region": "europe"}, format="json").json()
    assert body["analysis"] is None and body["verdict"]["kind"] == "not_market"
    assert fake_cala["calls"]["n"] == 0
