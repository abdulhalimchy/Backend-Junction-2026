import datetime

import pytest
from django.urls import reverse

from apps.alerts import scan
from apps.alerts.models import Alert, EntitySnapshot
from apps.core.cala import CalaError
from apps.events.models import Event
from apps.watchlist.tests.factories import WatchItemFactory

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 11, 14)
SLUSH_ID = "bc844d56-6ab4-452b-96bf-2661e86e592b"
TIDE = "11111111-1111-1111-1111-111111111111"  # a company
FJORD = "22222222-2222-2222-2222-222222222222"  # an investor
PORT = "33333333-3333-3333-3333-333333333333"  # a company Fjord backs
NEW_ROUND = "aaaaaaaa-0000-0000-0000-000000000001"
OLD_ROUND = "aaaaaaaa-0000-0000-0000-000000000002"
DEAL = "aaaaaaaa-0000-0000-0000-000000000003"


def src(name="tracxn.com", date="2026-10-30"):
    return {"name": name, "document": f"https://{name}/x", "date": date}


def edge(id, name="", role="ISSUER", date="", lead=False, entity_type="FundingRound"):
    props = {"role": role, "sources": [src()], "is_lead": lead}
    if date:
        props["event_date"] = date
    return {"id": id, "name": name, "entity_type": entity_type, "properties": props}


class FakeCala:
    """Serves canned entities by id and counts calls."""

    def __init__(self, entities):
        self.entities = entities
        self.calls = []

    def retrieve_entity(self, entity_id, query=None):
        self.calls.append(entity_id)
        e = self.entities[entity_id]
        if isinstance(e, Exception):
            raise e
        return e


def company(id, name, rounds=(), events=(), headcount=None):
    props = {"name": {"value": name}}
    if headcount:
        props["employee_count_range"] = {"value": headcount, "sources": [src("LinkedIn")]}
    return {
        "id": id,
        "name": name,
        "properties": props,
        "relationships": {
            "outgoing": {
                "PARTICIPATES_IN_FUNDING_ROUND": list(rounds),
                "PARTICIPATES_IN_EVENT": [
                    {
                        "id": e,
                        "name": "Slush 2026",
                        "properties": {"sources": [src("slush.org", "2026-11-11")]},
                    }
                    for e in events
                ],
            }
        },
    }


def round_entity(id, issuer, amount=None, label="SEED", leads=(), others=(), date="2026-10-30"):
    parts = [{"id": issuer[0], "name": issuer[1], "properties": {"role": "ISSUER"}}]
    parts += [
        {"id": f"l{i}", "name": n, "properties": {"role": "INVESTOR", "is_lead": True}}
        for i, n in enumerate(leads)
    ]
    parts += [{"id": f"o{i}", "name": n, "properties": {"role": "INVESTOR"}} for i, n in enumerate(others)]
    props = {"round_label": {"value": label, "sources": [src("press")]}, "event_date": {"value": date}}
    if amount:
        props["transaction_value"] = {"value": amount, "sources": [src("press")]}
        props["transaction_value_units"] = {"value": "EUR"}
    return {
        "id": id,
        "name": "round",
        "properties": props,
        "relationships": {"incoming": {"PARTICIPATES_IN_FUNDING_ROUND": parts}},
    }


@pytest.fixture
def slush():
    # Seeded by the events migration.
    return Event.objects.get(cala_event_id=SLUSH_ID)


@pytest.fixture
def cala():
    return FakeCala(
        {
            TIDE: company(
                TIDE,
                "Tidewise",
                rounds=[edge(NEW_ROUND, date="2026-10-30"), edge(OLD_ROUND, date="2019-03-01")],
                events=[SLUSH_ID],
                headcount=[11, 50],
            ),
            NEW_ROUND: round_entity(
                NEW_ROUND, (TIDE, "Tidewise"), 2_000_000, leads=["Fjord Ventures"], others=["Angel A"]
            ),
            OLD_ROUND: round_entity(OLD_ROUND, (TIDE, "Tidewise"), date="2019-03-01"),
        }
    )


def run(cala, pairs=None, **kw):
    return scan.scan(
        pairs if pairs is not None else scan.targets(), client=cala, today=TODAY, backfill_days=60, **kw
    )


def test_first_scan_reports_recent_round_and_slush_but_not_history(cala, user, slush):
    WatchItemFactory(user=user, cala_id=TIDE, name="Tidewise", tag="competitor")

    result = run(cala)

    assert (result.entities, result.alerts_created) == (1, 2)
    alerts = {a.kind: a for a in Alert.objects.filter(user=user)}
    round_alert = alerts[Alert.Kind.COMPETITOR_ROUND]
    assert round_alert.title == "Tidewise raised €2M (Seed)"
    assert round_alert.body == "Led by Fjord Ventures. With Angel A."
    assert round_alert.reason == "One of your competitors"
    assert round_alert.occurred_on == datetime.date(2026, 10, 30)
    assert round_alert.href == f"/company/{TIDE}"
    assert round_alert.sources  # every alert carries Cala's sources
    slush_alert = alerts[Alert.Kind.SLUSH_ATTENDEE]
    assert slush_alert.event == slush
    assert slush_alert.occurred_on == datetime.date(2026, 11, 11)
    assert OLD_ROUND not in cala.calls  # history is never looked up
    assert EntitySnapshot.objects.get(cala_id=TIDE).facts["headcount"]["value"] == "11–50"


def test_rerun_creates_no_duplicates(cala, user, slush):
    WatchItemFactory(user=user, cala_id=TIDE)
    run(cala)

    assert run(cala).alerts_created == 0
    assert Alert.objects.count() == 2


def test_every_watcher_gets_their_own_alert_and_reason(cala, user, slush):
    WatchItemFactory(user=user, cala_id=TIDE, tag="competitor")
    other = WatchItemFactory(cala_id=TIDE, tag="pipeline").user

    run(cala)

    mine = Alert.objects.get(user=user, kind__in=["competitor_round", "funding_round"])
    theirs = Alert.objects.get(user=other, kind__in=["competitor_round", "funding_round"])
    assert (mine.kind, mine.reason) == ("competitor_round", "One of your competitors")
    assert (theirs.kind, theirs.reason) == ("funding_round", "In your pipeline")
    assert cala.calls.count(TIDE) == 1  # one lookup per entity, however many watchers


def test_headcount_change_between_scans(cala, user, slush):
    WatchItemFactory(user=user, cala_id=TIDE)
    run(cala)
    cala.entities[TIDE]["properties"]["employee_count_range"]["value"] = [51, 200]

    assert run(cala).alerts_created == 1
    alert = Alert.objects.get(kind=Alert.Kind.TEAM_CHANGE)
    assert alert.title == "Tidewise now lists 51–200 employees"
    assert alert.body == "Was 11–50 on record at the previous check."


def test_undated_round_counts_only_when_new_since_last_scan(cala, user, slush):
    WatchItemFactory(user=user, cala_id=TIDE)
    run(cala)
    undated = "aaaaaaaa-0000-0000-0000-000000000009"
    cala.entities[TIDE]["relationships"]["outgoing"]["PARTICIPATES_IN_FUNDING_ROUND"].append(edge(undated))
    cala.entities[undated] = round_entity(undated, (TIDE, "Tidewise"), date="", label="SERIES_A")

    run(cala)

    assert Alert.objects.filter(title="Tidewise has a new Series a round on record").exists()


def test_founders_own_company_gives_profile_alerts(cala, user, slush):
    user.profile.cala_company_id = TIDE
    user.profile.save()

    run(cala)

    assert set(Alert.objects.filter(user=user).values_list("kind", "reason")) == {
        ("profile", "Your company"),
        ("slush_attendee", "Your company"),
    }


def test_investor_deal_explains_link_to_watched_company(user, slush):
    cala = FakeCala(
        {
            FJORD: {
                "id": FJORD,
                "name": "Fjord Ventures",
                "properties": {},
                "relationships": {
                    "outgoing": {
                        "PARTICIPATES_IN_FUNDING_ROUND": [
                            edge(DEAL, role="INVESTOR", date="2026-10-30", lead=True)
                        ],
                        "INVESTED_IN": [
                            {"id": PORT, "name": "PortBotics", "properties": {"sources": [src()]}}
                        ],
                    }
                },
            },
            DEAL: round_entity(DEAL, (PORT, "PortBotics"), 2_000_000, label="SEED", leads=["Fjord Ventures"]),
        }
    )
    WatchItemFactory(
        user=user, entity_type="investor", cala_id=FJORD, name="Fjord Ventures", tag="target_investor"
    )
    WatchItemFactory(user=user, cala_id=PORT, name="PortBotics", tag="competitor")

    run(cala, [("investor", FJORD)])

    alert = Alert.objects.get(user=user)
    assert alert.kind == "investor_activity"
    assert alert.title == "Fjord Ventures led PortBotics's Seed round (€2M)"
    assert alert.reason == "Backs PortBotics: one of your competitors"
    assert alert.href == f"/company/{PORT}"


def test_new_portfolio_company_needs_a_previous_scan(user, slush):
    investor = {
        "id": FJORD,
        "name": "Fjord Ventures",
        "properties": {},
        "relationships": {"outgoing": {"INVESTED_IN": []}},
    }
    cala = FakeCala({FJORD: investor})
    WatchItemFactory(user=user, entity_type="investor", cala_id=FJORD)
    run(cala)
    investor["relationships"]["outgoing"]["INVESTED_IN"].append(
        {"id": PORT, "name": "PortBotics", "properties": {"sources": [src()]}}
    )

    assert run(cala).alerts_created == 1
    assert Alert.objects.get().title == "Fjord Ventures invested in PortBotics"


def test_demo_ids_are_skipped_and_errors_do_not_stop_the_scan(cala, user, slush):
    broken = "44444444-4444-4444-4444-444444444444"
    cala.entities[broken] = CalaError("boom", 500)
    WatchItemFactory(user=user, cala_id="portbotics")
    WatchItemFactory(user=user, cala_id=broken)
    WatchItemFactory(user=user, cala_id=TIDE)

    result = run(cala)

    assert result.skipped == ["portbotics"]
    assert result.errors == {broken: "boom"}
    assert result.entities == 1
    assert "portbotics" not in cala.calls


def test_missing_key_aborts(user):
    WatchItemFactory(user=user, cala_id=TIDE)

    with pytest.raises(CalaError):
        run(FakeCala({TIDE: CalaError("no key", 401, "missing_key")}))


def test_targets_are_scoped_to_user(user):
    WatchItemFactory(user=user, cala_id=TIDE)
    WatchItemFactory(cala_id=PORT)
    WatchItemFactory(user=user, entity_type="person", cala_id="someone")

    assert scan.targets(user) == [("company", TIDE)]
    assert scan.targets() == [("company", TIDE), ("company", PORT)]


def test_money_format():
    assert scan.money(2_000_000, "EUR") == "€2M"
    assert scan.money(12_500_000, "usd") == "$12.5M"
    assert scan.money(18_000_000, "NOK") == "NOK 18M"
    assert scan.money(900, "GBP") == "£900"


def test_scan_endpoint_scans_own_watch_list(auth_client, user, cala, slush, monkeypatch, settings):
    # The endpoint scans as of the real today: reach back to October 2026, not to 2019.
    settings.ALERT_BACKFILL_DAYS = (datetime.date.today() - datetime.date(2026, 10, 1)).days
    WatchItemFactory(user=user, cala_id=TIDE)
    WatchItemFactory(cala_id=PORT)  # someone else's: not scanned
    monkeypatch.setattr(scan, "CalaClient", lambda: cala)

    response = auth_client.post(reverse("v1:alerts:alert-scan"))

    assert response.status_code == 200
    assert response.json() == {"entities": 1, "alerts_created": 2, "skipped": 0, "errors": {}}
    assert PORT not in cala.calls
    listed = auth_client.get(reverse("v1:alerts:alert-list")).json()["results"]
    assert {a["reason"] for a in listed} == {"On your watch list"}


def test_scan_endpoint_reports_cala_outage(auth_client, user, monkeypatch):
    WatchItemFactory(user=user, cala_id=TIDE)
    monkeypatch.setattr(scan, "CalaClient", lambda: FakeCala({TIDE: CalaError("no key", 401, "missing_key")}))

    response = auth_client.post(reverse("v1:alerts:alert-scan"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "cala_unavailable"


def test_undated_edges_are_looked_up_on_first_scan_and_kept_only_if_recent(user, slush):
    recent, old = "aaaaaaaa-0000-0000-0000-00000000000a", "aaaaaaaa-0000-0000-0000-00000000000b"
    cala = FakeCala(
        {
            TIDE: company(TIDE, "Tidewise", rounds=[edge(recent), edge(old)]),
            recent: round_entity(recent, (TIDE, "Tidewise"), 5_000_000, label="SERIES_A", date="2026-11-01"),
            old: round_entity(old, (TIDE, "Tidewise"), date="2018-05-01"),
        }
    )
    WatchItemFactory(user=user, cala_id=TIDE)

    run(cala)

    assert list(Alert.objects.values_list("title", flat=True)) == ["Tidewise raised €5M (Series a)"]
    assert run(cala).alerts_created == 0  # and the rerun neither repeats it nor surfaces the old one
