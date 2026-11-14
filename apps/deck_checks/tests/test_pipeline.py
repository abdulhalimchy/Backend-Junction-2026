# ruff: noqa: E501
import pytest
from django.urls import reverse

from apps.core.cala import CalaError
from apps.deck_checks import llm, pipeline
from apps.deck_checks.evidence import Evidence, EvidenceBuilder, normalise_name
from apps.deck_checks.models import DeckCheck

from .factories import DeckCheckFactory

pytestmark = pytest.mark.django_db

SRC = {
    "name": "Registry",
    "document": "https://example.test/r",
    "date": "2023-05-01",
    "date_precision": "month",
    "source_rank": "FILING",
}


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CALA_API_KEY = "test-key"


def patch_llm(monkeypatch, *, claims, verdicts, name="Northlight Robotics"):
    monkeypatch.setattr(llm, "extract_claims", lambda pdf: llm.DeckReading(name, [], "", claims))
    monkeypatch.setattr(llm, "verify_claims", lambda company, c, e: verdicts)


def patch_evidence(monkeypatch, evidence):
    monkeypatch.setattr(pipeline, "build_evidence", lambda client, **kw: ("Northlight Robotics", evidence))


CLAIMS = [
    {"slide": 1, "claim": "Founded in Helsinki in 2023", "category": "company"},
    {"slide": 9, "claim": "EUR 850k raised", "category": "funding"},
    {"slide": 6, "claim": "3 paying customers", "category": "traction"},
    {"slide": 4, "claim": "Market worth 4.2B", "category": "market"},
]


def test_pipeline_stores_verdicts_with_sources(monkeypatch):
    patch_evidence(
        monkeypatch,
        [Evidence("E1", "Founded 2023-05", [SRC]), Evidence("E2", "Raised 500k", [SRC | {"name": "Press"}])],
    )
    patch_llm(
        monkeypatch,
        claims=CLAIMS,
        verdicts=[
            {"index": 0, "status": "confirmed", "on_record": "Founded May 2023", "evidence_ids": ["E1"]},
            {"index": 1, "status": "differs", "on_record": "500k equity", "evidence_ids": ["E2"]},
            {
                "index": 2,
                "status": "confirmed",
                "on_record": "made up",
                "evidence_ids": [],
            },  # no evidence -> downgraded
            {
                "index": 3,
                "status": "differs",
                "on_record": "x",
                "evidence_ids": ["E99"],
            },  # unknown id -> downgraded
        ],
    )
    deck = DeckCheckFactory()

    pipeline.run_deck_check(deck.pk)

    deck.refresh_from_db()
    assert deck.status == DeckCheck.Status.COMPLETED
    assert deck.company_name == "Northlight Robotics"
    assert deck.checked_at
    claims = {c.slide: c for c in deck.claims.all()}
    assert claims[1].status == "confirmed" and claims[1].sources == [SRC]
    assert claims[9].status == "differs" and claims[9].sources[0]["name"] == "Press"
    assert claims[6].status == "cant_verify" and claims[6].on_record == "" and claims[6].sources == []
    assert claims[4].status == "cant_verify"


def test_rerun_replaces_old_claims(monkeypatch):
    patch_evidence(monkeypatch, [Evidence("E1", "f", [SRC])])
    patch_llm(monkeypatch, claims=CLAIMS[:2], verdicts=[])
    deck = DeckCheckFactory()
    pipeline.run_deck_check(deck.pk)
    pipeline.run_deck_check(deck.pk)
    assert deck.claims.count() == 2


def test_company_not_in_cala_marks_everything_cant_verify(monkeypatch):
    patch_evidence(monkeypatch, [])
    patch_llm(monkeypatch, claims=CLAIMS, verdicts=[])
    deck = DeckCheckFactory()
    pipeline.run_deck_check(deck.pk)
    deck.refresh_from_db()
    assert deck.status == "completed"
    assert set(deck.claims.values_list("status", flat=True)) == {"cant_verify"}


def test_missing_cala_key_fails_cleanly(settings):
    settings.CALA_API_KEY = ""
    deck = DeckCheckFactory()
    pipeline.run_deck_check(deck.pk)
    deck.refresh_from_db()
    assert deck.status == "failed"
    assert "CALA_API_KEY" in deck.error


def test_no_claims_found_fails(monkeypatch):
    patch_llm(monkeypatch, claims=[], verdicts=[])
    deck = DeckCheckFactory()
    pipeline.run_deck_check(deck.pk)
    deck.refresh_from_db()
    assert deck.status == "failed"
    assert "No checkable claims" in deck.error


def test_upstream_error_is_recorded(monkeypatch):
    def boom(pdf):
        raise llm.LLMError("Anthropic API error: overloaded")

    monkeypatch.setattr(llm, "extract_claims", boom)
    deck = DeckCheckFactory()
    pipeline.run_deck_check(deck.pk)
    deck.refresh_from_db()
    assert deck.status == "failed" and "overloaded" in deck.error


def test_unexpected_crash_does_not_leave_processing(monkeypatch):
    monkeypatch.setattr(llm, "extract_claims", lambda pdf: 1 / 0)
    deck = DeckCheckFactory()
    pipeline.run_deck_check(deck.pk)
    deck.refresh_from_db()
    assert deck.status == "failed"
    assert "ZeroDivision" not in deck.error  # internals are not leaked


def test_retry_only_for_failed(auth_client, user, monkeypatch):
    ran = []
    monkeypatch.setattr("apps.deck_checks.services.enqueue", ran.append)
    ok = DeckCheckFactory(user=user, status="completed")
    bad = DeckCheckFactory(user=user, status="failed", error="x")
    url = lambda d: reverse("v1:deck_checks:deck-check-retry", kwargs={"pk": d.pk})  # noqa: E731

    assert auth_client.post(url(ok)).status_code == 400
    assert auth_client.post(url(bad)).status_code == 202
    bad.refresh_from_db()
    assert bad.status == "pending" and bad.error == ""


# --- evidence ---------------------------------------------------------------


class FakeCala:
    def __init__(self):
        self.entities = {
            "co1": {
                "id": "co1",
                "name": "Northlight Robotics Oy",
                "properties": {
                    "founding_date": {"value": "2023-05-01", "sources": [SRC]},
                    "founding_date__precision": {"value": "MONTH", "sources": []},
                    "employee_count_range": {"value": [11, 50], "sources": [SRC]},
                    "total_funding": {"value": 850000, "sources": [SRC]},
                    "total_funding_units": {"value": "EUR", "sources": [SRC]},
                    "unsourced": {"value": 1, "sources": []},
                },
                "relationships": {
                    "outgoing": {
                        "PARTICIPATES_IN_FUNDING_ROUND": [
                            {
                                "id": "r1",
                                "name": "Round",
                                "entity_type": "FundingRound",
                                "properties": {"role": "ISSUER"},
                            }
                        ]
                    },
                    "incoming": {
                        "INVESTED_IN": [
                            {"id": "i1", "name": "Harbour Seed", "properties": {"sources": [SRC]}}
                        ]
                    },
                },
            },
            "r1": {
                "id": "r1",
                "properties": {
                    "round_label": {"value": "PRE_SEED", "sources": [SRC]},
                    "instrument": {"value": "EQUITY", "sources": [SRC]},
                    "transaction_value": {"value": 500000, "sources": [SRC]},
                    "transaction_value_units": {"value": "EUR", "sources": [SRC]},
                    "announcement_date": {"value": "2023-11-21", "sources": [SRC]},
                },
            },
        }

    def search_entities(self, name, types=None, limit=8):
        return [{"id": "x", "name": "Northlight Group"}, {"id": "co1", "name": "Northlight Robotics Oy"}]

    def retrieve_entity(self, entity_id, query=None):
        return self.entities.get(
            entity_id, {"id": entity_id, "name": "Northlight Group", "properties": {}, "relationships": {}}
        )


def test_normalise_name_drops_legal_suffix():
    assert normalise_name("Northlight Robotics Oy") == normalise_name("northlight robotics")


def test_evidence_from_cala_entity():
    builder = EvidenceBuilder(FakeCala())
    entity = builder.resolve_company(
        "", ["Northlight Robotics"]
    )  # exact match after normalisation, not the fuzzy first hit
    builder.add_company_facts(entity)
    texts = [e.text for e in builder.items]

    assert any("founded 2023-05." in t for t in texts)  # precision applied
    assert any("11-50 employees" in t for t in texts)
    assert any("850,000 EUR" in t for t in texts)
    assert any("Harbour Seed" in t for t in texts)
    assert any("Pre-seed (equity), 500,000 EUR, 2023-11-21" in t for t in texts)
    assert all(e.sources for e in builder.items)  # never an unsourced fact


def test_unmatched_company_name_gives_no_evidence():
    assert EvidenceBuilder(FakeCala()).resolve_company("", ["Totally Different Ltd"]) is None


def test_renamed_company_matches_by_alias():
    cala = FakeCala()
    cala.entities["co1"]["properties"]["aliases"] = {"value": ["Northlight", "Hullbot"], "sources": []}
    cala.search_entities = lambda name, types=None, limit=8: [{"id": "co1", "name": "Northlight Robotics Oy"}]
    assert EvidenceBuilder(cala).resolve_company("", ["Hullbot"])["id"] == "co1"


def test_matches_by_website_domain():
    cala = FakeCala()
    cala.entities["co1"]["properties"]["website"] = {
        "value": "https://www.northlight.example/",
        "sources": [SRC],
    }
    cala.search_entities = lambda name, types=None, limit=8: [{"id": "co1", "name": "Something Else"}]
    assert EvidenceBuilder(cala).resolve_company("", ["NL"], "northlight.example")["id"] == "co1"


def test_prefers_richest_of_several_exact_matches():
    cala = FakeCala()
    cala.entities["thin"] = {
        "id": "thin",
        "name": "Northlight Robotics",
        "properties": {},
        "relationships": {},
    }
    cala.search_entities = lambda name, types=None, limit=8: [
        {"id": "thin", "name": "Northlight Robotics"},
        {"id": "co1", "name": "Northlight Robotics Oy"},
    ]
    assert EvidenceBuilder(cala).resolve_company("", ["Northlight Robotics"])["id"] == "co1"


def test_founders_and_round_leads_are_evidence():
    cala = FakeCala()
    co = cala.entities["co1"]
    co["relationships"]["incoming"]["FOUNDED"] = [
        {"id": "p1", "name": "Anna Virtanen", "properties": {"sources": [SRC], "valid_since": "2023-05-01"}}
    ]
    cala.entities["r1"]["relationships"] = {
        "incoming": {
            "PARTICIPATES_IN_FUNDING_ROUND": [
                {
                    "id": "i1",
                    "name": "Harbour Seed",
                    "properties": {"role": "INVESTOR", "is_lead": True, "sources": [SRC]},
                },
                {
                    "id": "co1",
                    "name": "Northlight Robotics Oy",
                    "properties": {"role": "ISSUER", "sources": [SRC]},
                },
            ]
        }
    }
    b = EvidenceBuilder(cala)
    b.add_company_facts(co)
    texts = " ".join(e.text for e in b.items)
    assert "Anna Virtanen is recorded as a founder of" in texts
    assert "Led by Harbour Seed." in texts


def test_cala_error_type():
    assert CalaError("x", 429, "rate_limited").kind == "rate_limited"


# --- llm provider dispatch ------------------------------------------------------


def test_gemini_needs_key(settings):
    settings.DECK_CHECK_LLM_PROVIDER, settings.GEMINI_API_KEY = "gemini", ""
    with pytest.raises(llm.LLMError, match="GEMINI_API_KEY"):
        llm.extract_claims(b"%PDF-1.4")


def test_unknown_provider(settings):
    settings.DECK_CHECK_LLM_PROVIDER = "nope"
    with pytest.raises(llm.LLMError, match="Unknown"):
        llm.verify_claims("x", [], [])


def test_gemini_structured_output(settings, monkeypatch):
    settings.DECK_CHECK_LLM_PROVIDER, settings.GEMINI_API_KEY, settings.DECK_CHECK_LLM_MODEL = (
        "gemini",
        "k",
        "my-model",
    )
    seen = {}

    class Models:
        def generate_content(self, **kw):
            seen.update(kw)
            return type(
                "R",
                (),
                {
                    "text": '{"company_name": "Acme", "claims": [{"slide": 2, "claim": "Founded 2020", "category": "company"}, {"slide": 0, "claim": "bad", "category": "x"}]}'
                },
            )()

    monkeypatch.setattr(llm.genai, "Client", lambda api_key: type("C", (), {"models": Models()})())
    reading = llm.extract_claims(b"%PDF-1.4")
    name, claims = reading.company_name, reading.claims

    assert seen["model"] == "my-model"
    assert name == "Acme" and claims == [{"slide": 2, "claim": "Founded 2020", "category": "company"}]


def test_company_named_in_deck_wins_over_upload_hint(monkeypatch):
    seen = {}

    def fake_build(client, **kw):
        seen.update(kw)
        return "", []

    monkeypatch.setattr(pipeline, "build_evidence", fake_build)
    patch_llm(monkeypatch, claims=CLAIMS[:1], verdicts=[], name="AirBed&Breakfast")
    deck = DeckCheckFactory(company_name="Northlight Robotics")
    pipeline.run_deck_check(deck.pk)
    deck.refresh_from_db()
    assert seen["names"][0] == "AirBed&Breakfast"
    assert "Northlight Robotics" not in seen["names"]
    assert deck.company_name == "AirBed&Breakfast"
