# ruff: noqa: E501
from types import SimpleNamespace

from apps.startups import analysis, search_terms


def startup(**kw):
    base = {"pk": "s1", "name": "7Scholar", "domain": "7scholar.com", "industry": "Medical writing software",
            "description": "Medical writing workspace for pharma teams.", "stage": "", "headquarters": "",
            "cala_company_id": ""}  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def test_llm_searches_are_cleaned_and_deduplicated(monkeypatch):
    monkeypatch.setattr(search_terms.llm, "configured", lambda: True)
    answer = {
        "searches": [
            {
                "label": "AI medical writing",
                "entity": "startups",
                "field": "industry",
                "value": "A.I. medical writing",
            },
            {"label": "dup", "entity": "startups", "field": "industry", "value": "a i medical writing"},
            {
                "label": "Pharma docs",
                "entity": "bogus",
                "field": "description",
                "value": "regulatory=docs (pharma)",
            },
            {"label": "too short", "entity": "companies", "field": "industry", "value": "ai"},
        ]
    }
    monkeypatch.setattr(search_terms.llm, "generate_json", lambda *a: (answer, None))
    out = search_terms.plan_searches(startup())
    assert [q["query"] for q in out] == [
        "startups.industry=a i medical writing.return(name, country, founded_year)",
        "startups.description=regulatory docs pharma.return(name, country, founded_year)",
    ]


def test_falls_back_to_the_industry_when_the_llm_fails(monkeypatch):
    monkeypatch.setattr(search_terms.llm, "configured", lambda: True)

    def boom(*a):
        raise RuntimeError("down")

    monkeypatch.setattr(search_terms.llm, "generate_json", boom)
    out = search_terms.plan_searches(startup())
    assert out == [{"label": "Medical writing software",
                    "query": "startups.description=Medical writing software.return(name, country, founded_year)"}]  # fmt: skip
    assert search_terms.plan_searches(startup(industry="", description="")) == []


def test_every_search_contributes_companies():
    linked = lambda *names: [{"id": f"id-{n}", "name": n, "entity_type": "Company"} for n in names]  # noqa: E731
    results = [
        (["A1", "A2", "A3", "A4"], linked("A1", "A2", "A3", "A4")),
        (["B1", "7Scholar", "A1"], linked("B1", "A1")),
        (["C1"], linked("C1")),
    ]
    client = SimpleNamespace(search_entities=lambda *a: [])
    found = analysis._resolve_rows(client, startup(), results, limit=4)
    assert found["ids"] == ["id-A1", "id-B1", "id-C1", "id-A2"]
    assert found["per_search"] == [2, 1, 1]


def test_peers_check_drops_off_topic_companies_and_keeps_all_when_it_cannot_run(monkeypatch):
    peers = [
        {
            "id": "a",
            "name": "DocTool",
            "properties": {"description": {"value": "Regulatory writing software"}},
        },
        {"id": "b", "name": "DrugAI", "properties": {"description": {"value": "AI drug discovery"}}},
    ]
    monkeypatch.setattr(search_terms.llm, "configured", lambda: True)
    monkeypatch.setattr(search_terms.llm, "generate_json", lambda *a: ({"keep": ["a", "zzz"]}, None))
    kept, left_out = analysis.similar_peers(startup(), "peers", peers)
    assert [p["id"] for p in kept] == ["a"] and left_out == ["DrugAI"]

    # Competitors on record are never filtered.
    assert analysis.similar_peers(startup(), "competitors", peers) == (peers, [])

    # An empty or failed answer keeps every company.
    monkeypatch.setattr(search_terms.llm, "generate_json", lambda *a: ({"keep": []}, None))
    assert analysis.similar_peers(startup(), "peers", peers) == (peers, [])
