import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.markets import analysis, planner
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
    return lambda client, plan: {
        "question": plan["size_question"],
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

    def plan(text, known, year):
        market = "fintech" if "fin" in text.lower() else "edtech"
        place = "Finland" if "finland" in text.lower() else "Worldwide"
        countries = ["Finland"] if place == "Finland" else []
        data = {"market": market, "place": place, "countries": countries}
        return {
            "kind": "market",
            "reason": "",
            "suggestions": [],
            "plan": planner.complete_plan(data, text, year),
            "tokens": {},
        }

    monkeypatch.setattr("apps.markets.planner.plan_market", plan)
    return {"calls": calls, "players": players}


def test_requires_auth(api_client):
    assert api_client.get(LIST_URL).status_code == 401


def test_open_runs_once_and_stores_history(auth_client, fake_cala):
    res = auth_client.post(LIST_URL, {"text": "Ed Tech startups"}, format="json")
    assert res.status_code == 200
    a = res.json()["analysis"]
    assert a["sector"] == "edtech" and a["status"] == "ready"
    assert a["data"]["summary"]["headline"]["text"] == "edtech: 4 facts"
    assert a["data"]["players"]["companies"][0]["name"] == "Acme"
    assert fake_cala["calls"]["n"] == 4

    # Opening it again reads the stored result: no new Cala calls.
    auth_client.post(LIST_URL, {"text": "edtech"}, format="json")
    assert fake_cala["calls"]["n"] == 4
    history = auth_client.get(LIST_URL).json()
    assert [h["sector"] for h in history] == ["edtech"]
    assert history[0]["headline"] == "edtech: 4 facts"


def test_refresh_updates_and_records_changes(auth_client, fake_cala):
    a = auth_client.post(LIST_URL, {"text": "edtech"}, format="json").json()["analysis"]
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
    a = auth_client.post(LIST_URL, {"text": "edtech"}, format="json").json()["analysis"]
    auth_client.post(LIST_URL, {"text": "fintech in Finland"}, format="json")
    assert MarketAnalysis.objects.filter(user=user).count() == 2

    assert auth_client.delete(reverse("v1:markets:analysis", args=[a["id"]])).status_code == 204
    assert MarketAnalysis.objects.filter(user=user).count() == 1

    assert auth_client.delete(LIST_URL).json()["deleted"] == 1
    assert not MarketAnalysis.objects.filter(user=user).exists()


def test_non_market_text_spends_nothing(auth_client, fake_cala, monkeypatch):
    monkeypatch.setattr(
        "apps.markets.planner.plan_market",
        lambda text, known, year: {
            "kind": "not_market",
            "reason": "A task.",
            "suggestions": [],
            "plan": None,
            "tokens": {},
        },
    )
    body = auth_client.post(LIST_URL, {"text": "fix my code"}, format="json").json()
    assert body["analysis"] is None and body["verdict"]["kind"] == "not_market"
    assert fake_cala["calls"]["n"] == 0


def test_free_text_place_is_stored(auth_client, fake_cala):
    a = auth_client.post(LIST_URL, {"text": "seed fintech in Finland"}, format="json").json()["analysis"]
    assert a["sector"] == "fintech" and a["place"] == "Finland" and a["region"] == "finland"
    assert a["plan"]["countries"] == ["Finland"]
    assert a["plan"]["players_query"].startswith("companies.industry=fintech.location=Finland")


def _start_without_running(monkeypatch, settings):
    # The run is started but its thread never runs, so it stays "loading" for the test to stop.
    settings.DECK_CHECK_ASYNC = True
    monkeypatch.setattr(analysis, "_run_in_thread", lambda *a: None)


def test_stop_first_run_removes_it(auth_client, fake_cala, monkeypatch, settings, user):
    _start_without_running(monkeypatch, settings)
    a = auth_client.post(LIST_URL, {"text": "edtech"}, format="json").json()["analysis"]
    assert a["status"] == "loading" and a["can_stop"]
    body = auth_client.post(reverse("v1:markets:analysis-cancel", args=[a["id"]])).json()
    assert body == {"deleted": True, "analysis": None}
    assert not MarketAnalysis.objects.filter(user=user).exists()


def test_stop_refresh_rolls_back(auth_client, fake_cala, monkeypatch, settings):
    a = auth_client.post(LIST_URL, {"text": "edtech"}, format="json").json()["analysis"]
    _start_without_running(monkeypatch, settings)
    auth_client.post(reverse("v1:markets:analysis-refresh", args=[a["id"]]))
    row = MarketAnalysis.objects.get(pk=a["id"])
    assert row.status == "loading" and row.runs == 2

    body = auth_client.post(reverse("v1:markets:analysis-cancel", args=[a["id"]])).json()
    restored = body["analysis"]
    assert restored["status"] == "ready"
    assert restored["data"]["players"]["companies"][0]["name"] == "Acme"

    # The stopped run's late writes are discarded.
    fake_cala["players"]["companies"] = [{"name": "Late", "rounds": [], "investors": []}]
    analysis.run(a["id"], 2)
    row.refresh_from_db()
    assert row.status == "ready" and row.data["players"]["companies"][0]["name"] == "Acme"


def test_players_query_is_validated():
    built = planner.players_query("drop table", "ai chips", "Europe")
    assert built.startswith("companies.industry=ai chips.location=Europe.order_by=funding DESC.limit=12")
    q = planner.players_query("companies.industry=ai chips.limit=50", "ai chips", "")
    assert q == "companies.industry=ai chips.limit=12.return(name, country, funding)"
