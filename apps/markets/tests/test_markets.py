import pytest
from django.urls import reverse

from apps.markets import summary
from apps.markets.llm import LLMError, TokenUsage

pytestmark = pytest.mark.django_db

GUARD_URL = reverse("v1:markets:guard")
SUMMARY_URL = reverse("v1:markets:summary")

SRC = {"name": "Grand View", "document": "https://www.grandviewresearch.com/x", "date": "2025-01-01"}
FACTS = [
    {
        "id": "S1",
        "text": "The global mobile application market was valued at USD 252.89 billion in 2023, "
        "growing at a CAGR of 14.3% from 2024 to 2030.",
        "sources": [SRC],
    },
    {"id": "D1", "text": "Acme raised EUR 12 million in a Series A in March 2025.", "sources": [SRC]},
]


def fake_llm(data):
    def call(system, prompt, schema):
        return data, TokenUsage(input=100, output=20, thinking=5, total=125)

    return call


def test_requires_auth(api_client):
    assert api_client.post(GUARD_URL, {"text": "edtech"}).status_code == 401
    assert api_client.post(SUMMARY_URL, {}).status_code == 401


def test_guard_returns_verdict(auth_client, monkeypatch):
    monkeypatch.setattr(
        "apps.markets.guard.generate_json",
        fake_llm({"kind": "market", "reason": "", "suggestions": [], "canonical": "  Mobile   Apps "}),
    )
    res = auth_client.post(GUARD_URL, {"text": "mobile apps", "known": ["edtech"]})

    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["kind"] == "market"
    assert body["canonical"] == "mobile apps"
    assert body["tokens"]["total"] == 125


def test_guard_llm_failure_is_503(auth_client, monkeypatch):
    def boom(*a):
        raise LLMError("GEMINI_API_KEY is not set")

    monkeypatch.setattr("apps.markets.guard.generate_json", boom)
    res = auth_client.post(GUARD_URL, {"text": "edtech"})

    assert res.status_code == 503
    assert "GEMINI_API_KEY" in res.json()["error"]["message"]


def test_summary_keeps_checked_claims_and_drops_invented_numbers(auth_client, monkeypatch):
    raw = {
        "headline": {"text": "Mobile apps were worth USD 252.89 billion in 2023.", "factIds": ["S1"]},
        "bullets": [
            {"text": "Acme raised EUR 12 million.", "factIds": ["D1"]},
            {"text": "The market will reach USD 600 billion.", "factIds": ["S1"]},  # invented number
            {"text": "Unsupported.", "factIds": ["X9"]},  # unknown fact
        ],
        "sizes": [
            {
                "publisher": "unknown",
                "scope": "global mobile application market",
                "kind": "size",
                "year": 2023,
                "amount": 252.89,
                "scale": "billion",
                "currency": "USD",
                "cagrPercent": 14.3,
                "cagrPeriod": "2024-2030",
                "quote": "USD 252.89 billion in 2023",
                "factId": "S1",
            },
            {  # quote not in the fact
                "publisher": "X",
                "scope": "s",
                "kind": "forecast",
                "year": 2030,
                "amount": 600,
                "scale": "billion",
                "currency": "USD",
                "quote": "USD 600 billion by 2030",
                "factId": "S1",
            },
        ],
        "insights": [],
    }
    monkeypatch.setattr("apps.markets.summary.generate_json", fake_llm(raw))
    res = auth_client.post(
        SUMMARY_URL, {"market": "mobile applications", "region": "Anywhere", "year": 2026, "facts": FACTS}
    )

    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["headline"]["text"].startswith("Mobile apps")
    assert [b["text"] for b in body["bullets"]] == ["Acme raised EUR 12 million."]
    assert len(body["sizes"]) == 1
    size = body["sizes"][0]
    assert size["value"] == {"amount": 252.89e9, "currency": "USD"}
    assert size["cagr"] == {"percent": 14.3, "period": "2024-2030"}
    assert size["publisher"] == "grandviewresearch.com"  # unnamed publisher falls back to the site
    assert size["sources"] == [SRC]
    assert body["checked"] == 6
    assert len(body["dropped"]) == 3
    assert body["fact_count"] == 2
    assert body["tokens"]["total"] == 125


def test_summary_rejects_empty_facts(auth_client):
    res = auth_client.post(SUMMARY_URL, {"market": "m", "region": "r", "year": 2026, "facts": []})
    assert res.status_code == 400


def test_numbers_skip_labels():
    assert summary._numbers("Q3 2024 saw 5G and B2B deals of 1,250.5 units") == [2024.0, 1250.5]


def test_cagr_dropped_when_fact_does_not_state_it():
    raw = {
        "headline": None,
        "bullets": [],
        "insights": [],
        "sizes": [
            {
                "publisher": "GV",
                "scope": "s",
                "kind": "size",
                "year": 2023,
                "amount": 12,
                "scale": "million",
                "currency": "EUR",
                "cagrPercent": 9,
                "quote": "EUR 12 million",
                "factId": "D1",
            }
        ],
    }
    out = summary.check(raw, FACTS)
    assert out["sizes"][0]["cagr"] is None
