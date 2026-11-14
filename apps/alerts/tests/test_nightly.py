import datetime
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.alerts import nightly, scan
from apps.alerts.models import Alert, ScanRun
from apps.core.cala import CalaError
from apps.watchlist.tests.factories import WatchItemFactory

from .test_scan import PORT, TIDE, FakeCala, cala, slush  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db

HEL = ZoneInfo("Europe/Helsinki")


@pytest.fixture(autouse=True)
def schedule(settings):
    settings.ALERT_SCAN_TIME = "02:00"
    settings.ALERT_SCAN_TIMEZONE = "Europe/Helsinki"
    # Scans run as of the real today: reach back far enough to keep the October 2026 round recent.
    settings.ALERT_BACKFILL_DAYS = (datetime.date.today() - datetime.date(2026, 10, 1)).days


def at(day, hour, minute=0):
    return datetime.datetime(2026, 11, day, hour, minute, tzinfo=HEL)


def started(trigger, status, when):
    run = ScanRun.objects.create(trigger=trigger, status=status)
    ScanRun.objects.filter(pk=run.pk).update(started_at=when)
    return run


def test_slots_are_local_time():
    assert nightly.last_slot(at(15, 1, 59)) == at(14, 2)
    assert nightly.last_slot(at(15, 2)) == at(15, 2)
    assert nightly.next_slot(at(15, 1, 59)) == at(15, 2)
    assert nightly.next_slot(at(15, 14)) == at(16, 2)


def test_due_once_per_night_and_catches_up_after_downtime():
    now = at(15, 9)  # the server was down at 02:00
    assert nightly.is_due(now)

    started(ScanRun.Trigger.NIGHTLY, ScanRun.Status.OK, at(15, 9))
    assert not nightly.is_due(at(15, 10))
    assert not nightly.is_due(at(16, 1, 59))
    assert nightly.is_due(at(16, 2))


def test_other_runs_do_not_count_as_the_nightly_one():
    started(ScanRun.Trigger.MANUAL, ScanRun.Status.OK, at(15, 3))
    started(ScanRun.Trigger.WATCH, ScanRun.Status.OK, at(15, 3))
    assert nightly.is_due(at(15, 4))


def test_failed_run_is_retried_after_an_hour_and_stale_runs_do_not_block():
    started(ScanRun.Trigger.NIGHTLY, ScanRun.Status.FAILED, at(15, 2))
    assert not nightly.is_due(at(15, 2, 30))
    assert nightly.is_due(at(15, 3, 1))

    ScanRun.objects.all().delete()
    started(ScanRun.Trigger.NIGHTLY, ScanRun.Status.RUNNING, at(15, 2))
    assert not nightly.is_due(at(15, 4))
    assert nightly.is_due(at(15, 5, 1))  # the process died mid-run


def test_run_if_due_scans_every_watch_list_and_records_the_run(user, cala, slush, monkeypatch):  # noqa: F811
    WatchItemFactory(user=user, cala_id=TIDE, name="Tidewise")
    WatchItemFactory(cala_id="portbotics")  # someone else's demo entry: skipped
    monkeypatch.setattr(scan, "CalaClient", lambda: cala)

    record = nightly.run_if_due()

    assert record.trigger == ScanRun.Trigger.NIGHTLY
    assert record.status == ScanRun.Status.OK
    assert (record.entities, record.alerts_created, record.skipped) == (1, 2, 1)
    assert record.finished_at is not None
    assert nightly.run_if_due() is None  # done for tonight
    # Alerts know which watched entity they are about.
    assert set(Alert.objects.values_list("entity_type", "cala_id")) == {("company", TIDE)}


def test_run_if_due_records_a_cala_outage(user, monkeypatch):
    WatchItemFactory(user=user, cala_id=TIDE)
    monkeypatch.setattr(scan, "CalaClient", lambda: FakeCala({TIDE: CalaError("no key", 401, "missing_key")}))

    record = nightly.run_if_due()

    assert record.status == ScanRun.Status.FAILED
    assert "no key" in record.errors[""]


def test_schedule_endpoint(auth_client, user):
    started(ScanRun.Trigger.NIGHTLY, ScanRun.Status.OK, at(15, 2))
    started(ScanRun.Trigger.MANUAL, ScanRun.Status.OK, at(15, 10))  # someone else's
    started(ScanRun.Trigger.NIGHTLY, ScanRun.Status.RUNNING, at(15, 12))  # not finished: not shown

    body = auth_client.get(reverse("v1:alerts:alert-schedule")).json()

    assert body["time"] == "02:00"
    assert body["timezone"] == "Europe/Helsinki"
    assert body["last_nightly"]["status"] == "ok"
    assert body["last_manual"] is None
    assert body["next_run_at"]


def test_manual_scan_is_recorded(auth_client, user, cala, slush, monkeypatch):  # noqa: F811
    WatchItemFactory(user=user, cala_id=TIDE)
    monkeypatch.setattr(scan, "CalaClient", lambda: cala)

    auth_client.post(reverse("v1:alerts:alert-scan"))

    body = auth_client.get(reverse("v1:alerts:alert-schedule")).json()
    assert body["last_manual"]["entities"] == 1


def test_alerts_filter_by_watched_entity(auth_client, user, cala, slush, monkeypatch):  # noqa: F811
    WatchItemFactory(user=user, cala_id=TIDE)
    monkeypatch.setattr(scan, "CalaClient", lambda: cala)
    auth_client.post(reverse("v1:alerts:alert-scan"))

    url = reverse("v1:alerts:alert-list")
    assert len(auth_client.get(url, {"cala_id": TIDE}).json()["results"]) == 2
    assert auth_client.get(url, {"cala_id": PORT}).json()["results"] == []


def test_watch_list_shows_unread_alerts_and_last_check(auth_client, user, cala, slush, monkeypatch):  # noqa: F811
    WatchItemFactory(user=user, cala_id=TIDE, name="Tidewise")
    WatchItemFactory(user=user, cala_id="portbotics", name="PortBotics")
    monkeypatch.setattr(scan, "CalaClient", lambda: cala)
    auth_client.post(reverse("v1:alerts:alert-scan"))
    Alert.objects.filter(pk=Alert.objects.filter(user=user).first().pk).update(read_at=at(15, 9))

    items = {i["name"]: i for i in auth_client.get(reverse("v1:watchlist:watch-list")).json()["results"]}

    assert items["Tidewise"]["unread_alerts"] == 1
    assert items["Tidewise"]["last_checked_at"] is not None
    assert items["Tidewise"]["in_cala"] is True
    assert items["PortBotics"] == items["PortBotics"] | {
        "unread_alerts": 0,
        "last_checked_at": None,
        "in_cala": False,
    }


def test_watching_scans_the_new_entity_right_away(auth_client, user, cala, slush, monkeypatch, settings):  # noqa: F811
    settings.ALERT_SCAN_ON_WATCH = True
    monkeypatch.setattr(scan, "CalaClient", lambda: cala)
    url = reverse("v1:watchlist:watch-list")

    body = auth_client.post(url, {"entity_type": "company", "cala_id": TIDE, "name": "Tidewise"}).json()

    assert body["unread_alerts"] == 2
    assert body["last_checked_at"] is not None
    assert ScanRun.objects.get().trigger == ScanRun.Trigger.WATCH
    # Watching again (or a demo id) does not scan.
    auth_client.post(url, {"entity_type": "company", "cala_id": TIDE, "name": "Tidewise"})
    auth_client.post(url, {"entity_type": "company", "cala_id": "portbotics", "name": "PortBotics"})
    assert ScanRun.objects.count() == 1


def test_scan_on_watch_failure_does_not_break_watching(auth_client, user, monkeypatch, settings):
    settings.ALERT_SCAN_ON_WATCH = True
    monkeypatch.setattr(scan, "CalaClient", lambda: FakeCala({TIDE: CalaError("down", 503, "unavailable")}))

    response = auth_client.post(
        reverse("v1:watchlist:watch-list"), {"entity_type": "company", "cala_id": TIDE, "name": "Tidewise"}
    )

    assert response.status_code == 201
    assert response.json()["unread_alerts"] == 0
