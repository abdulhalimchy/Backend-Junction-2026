import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_health_check_is_public_and_reports_database(api_client):
    response = api_client.get(reverse("v1:core:health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_unauthenticated_request_returns_error_envelope(api_client, db):
    response = api_client.get("/api/v1/accounts/me/")

    assert response.status_code == 401
    assert response.json()["error"]["status_code"] == 401
