import pytest
from django.urls import reverse

from apps.portfolio import analysis
from apps.portfolio.models import MonitoredCompetitor, PortfolioAnalysis, PortfolioCompany

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:portfolio:portfolio-company-list")
ANALYSES_URL = reverse("v1:portfolio:portfolio-company-analyses")
REFRESH_URL = reverse("v1:portfolio:portfolio-company-refresh-analyses")
ACME_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def cala_on(settings):
    settings.CALA_API_KEY = "test"


def test_rounds_are_looked_up_once_when_a_company_in_cala_is_saved(auth_client, user, fake_builders):
    PortfolioCompany.objects.create(user=user, name="By hand")  # not in Cala: nothing to look up
    res = auth_client.post(LIST_URL, {"name": "Acme"}, format="json")
    assert res.status_code == 201
    assert fake_builders == []

    c = PortfolioCompany.objects.create(user=user, name="Acme", cala_company_id=ACME_ID)
    auth_client.patch(
        reverse("v1:portfolio:portfolio-company-detail", kwargs={"pk": c.pk}), {"notes": "x"}, format="json"
    )
    auth_client.patch(
        reverse("v1:portfolio:portfolio-company-detail", kwargs={"pk": c.pk}), {"notes": "y"}, format="json"
    )
    assert fake_builders == [("rounds", "Acme")]  # the first save only
    rounds = auth_client.get(ANALYSES_URL).json()
    assert [(a["company"], a["kind"], a["status"]) for a in rounds] == [(str(c.pk), "rounds", "ready")]


def test_refresh_runs_every_kind_and_syncs_competitors(auth_client, user, cala_on, fake_builders):
    acme = PortfolioCompany.objects.create(user=user, name="Acme", cala_company_id=ACME_ID)
    PortfolioCompany.objects.create(user=user, name="Notes only", description="Marine robots")
    PortfolioCompany.objects.create(user=user, name="Empty")  # nothing to analyse it on

    res = auth_client.post(REFRESH_URL, {}, format="json")
    assert res.status_code == 202, res.json()
    assert sorted(fake_builders) == [
        ("competitors", "Acme"),
        ("competitors", "Notes only"),
        ("investors", "Acme"),
        ("investors", "Notes only"),
        ("rounds", "Acme"),  # rounds need the company in Cala
    ]
    assert list(MonitoredCompetitor.objects.filter(company=acme).values_list("cala_id", "name")) == [
        ("c-1", "Rival")
    ]

    # One company and kind; a competitor that is itself in the portfolio is not monitored.
    PortfolioCompany.objects.create(user=user, name="Rival", cala_company_id="c-1")
    res = auth_client.post(REFRESH_URL, {"company": str(acme.pk), "kinds": ["competitors"]}, format="json")
    assert [a["kind"] for a in res.json()] == ["competitors"]
    assert not MonitoredCompetitor.objects.filter(company=acme).exists()


def test_a_run_in_progress_is_not_restarted(user, fake_builders):
    acme = PortfolioCompany.objects.create(user=user, name="Acme", cala_company_id=ACME_ID)
    assert analysis.mark_loading(acme, "rounds") is not None
    assert analysis.start(acme, ["rounds"]) == []
    assert fake_builders == []


def test_failure_keeps_the_previous_result(user, monkeypatch):
    acme = PortfolioCompany.objects.create(user=user, name="Acme", cala_company_id=ACME_ID)
    analysis.start(acme, ["rounds"])

    def boom(company, client):
        raise RuntimeError("Cala is down")

    monkeypatch.setitem(analysis.BUILDERS, analysis.Kind.ROUNDS, boom)
    analysis.start(acme, ["rounds"])
    a = PortfolioAnalysis.objects.get(company=acme)
    assert (a.status, a.error, a.data["rounds"]) == ("failed", "Cala is down", [])


def test_refresh_needs_cala_and_own_company(auth_client, user, settings, cala_on):
    other = PortfolioCompany.objects.create(
        user=PortfolioCompany._meta.get_field("user").related_model.objects.create(email="x@example.com"),
        name="Theirs",
    )
    assert auth_client.post(REFRESH_URL, {"company": str(other.pk)}, format="json").status_code == 404
    settings.CALA_API_KEY = ""
    assert auth_client.post(REFRESH_URL, {}, format="json").status_code == 503
