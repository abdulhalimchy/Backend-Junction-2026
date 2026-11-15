import pytest

from apps.portfolio import analysis


@pytest.fixture(autouse=True)
def fake_builders(monkeypatch):
    """Analyses never reach Cala in tests. Each builder records the company it ran for."""
    calls = []

    def builder(kind, data):
        def build(company, client):
            calls.append((kind, company.name))
            return data(company) if callable(data) else data

        return build

    monkeypatch.setattr(analysis, "CalaClient", lambda: None)
    monkeypatch.setitem(
        analysis.BUILDERS,
        analysis.Kind.ROUNDS,
        builder("rounds", {"rounds": [], "raised": None, "capped": False}),
    )
    monkeypatch.setitem(
        analysis.BUILDERS,
        analysis.Kind.COMPETITORS,
        builder("competitors", {"mode": "competitors", "companies": [{"id": "c-1", "name": "Rival"}]}),
    )
    monkeypatch.setitem(analysis.BUILDERS, analysis.Kind.INVESTORS, builder("investors", {"investors": []}))
    return calls
