import pytest
from django.urls import reverse

from apps.core.cala import CalaClient, CalaError

pytestmark = pytest.mark.django_db


def url(path):
    return reverse("v1:core:cala", args=[path])


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(self, method, path, *, json=None, params=None):
        seen.append((method, path, json, params))
        return {"ok": True, "path": path}

    monkeypatch.setattr(CalaClient, "_request", fake)
    return seen


def test_requires_auth(api_client):
    assert api_client.post(url("v1/knowledge/search"), {"input": "x"}, format="json").status_code == 401


def test_forwards_allowed_requests(auth_client, calls):
    body = {"input": "edtech", "explainability": False, "return_entities": True}
    assert auth_client.post(url("v1/knowledge/search"), body, format="json").json() == {
        "ok": True,
        "path": "/v1/knowledge/search",
    }
    auth_client.get(url("v1/entities") + "?name=Aiven&limit=5&entity_types=Company&entity_types=Organization")
    auth_client.post(url("v1/entities/abc-123"), {"properties": ["name"]}, format="json")
    auth_client.get(url("v1/entities/abc-123/introspection"))
    assert calls[0] == ("POST", "/v1/knowledge/search", body, None)
    assert calls[1][3] == [
        ("name", "Aiven"),
        ("limit", "5"),
        ("entity_types", "Company"),
        ("entity_types", "Organization"),
    ]
    assert calls[2][:3] == ("POST", "/v1/entities/abc-123", {"properties": ["name"]})
    assert calls[3][:2] == ("GET", "/v1/entities/abc-123/introspection")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "v1/knowledge/search"),
        ("POST", "v1/entities"),
        ("DELETE", "v1/entities/x"),
        ("GET", "v2/admin"),
        ("GET", "v1/entities/../secrets"),
    ],
)
def test_rejects_other_requests(auth_client, calls, method, path):
    res = auth_client.generic(method, url(path))
    assert res.status_code in (404, 405)
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (CalaError("Cala credit balance is empty", 402, "no_credit"), 402, "no_credit"),
        (CalaError("Cala rate limit exceeded", 429, "rate_limited"), 429, "rate_limited"),
        (CalaError("CALA_API_KEY is not set", 401, "missing_key"), 503, "missing_key"),
        (CalaError("Cala rejected the request: bad", 422, "invalid_request"), 422, "invalid_request"),
        (CalaError("Cala returned 404: nope", 404), 502, "upstream"),
        (CalaError("Could not reach Cala", 0), 502, "upstream"),
    ],
)
def test_maps_cala_errors(auth_client, monkeypatch, error, status, code):
    def fail(self, *a, **k):
        raise error

    monkeypatch.setattr(CalaClient, "_request", fail)
    res = auth_client.post(url("v1/knowledge/query"), {"input": "x"}, format="json")
    assert res.status_code == status
    assert res.json()["error"]["code"] == code
    assert res.json()["error"]["message"] == str(error)


def test_gateway_key_works_without_a_user(api_client, calls, settings):
    settings.CALA_GATEWAY_KEY = "s3cret"
    res = api_client.post(
        url("v1/knowledge/search"), {"input": "x"}, format="json", HTTP_X_GATEWAY_KEY="s3cret"
    )
    assert res.status_code == 200


@pytest.mark.parametrize(("configured", "sent"), [("s3cret", "wrong"), ("s3cret", ""), ("", "")])
def test_wrong_or_unset_gateway_key_is_refused(api_client, calls, settings, configured, sent):
    settings.CALA_GATEWAY_KEY = configured
    res = api_client.post(url("v1/knowledge/search"), {"input": "x"}, format="json", HTTP_X_GATEWAY_KEY=sent)
    assert res.status_code in (401, 403)
    assert calls == []
