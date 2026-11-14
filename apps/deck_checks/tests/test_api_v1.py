import pytest
from django.urls import reverse

from apps.deck_checks.models import DeckCheck

from .factories import DeckCheckFactory, DeckClaimFactory, pdf_upload

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:deck_checks:deck-check-list")


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def detail_url(deck_check):
    return reverse("v1:deck_checks:deck-check-detail", kwargs={"pk": deck_check.pk})


def test_upload_pdf_creates_pending_check(auth_client, user, media_root):
    response = auth_client.post(
        LIST_URL, {"file": pdf_upload(), "cala_company_id": "northlight"}, format="multipart"
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["status"] == "pending"
    assert body["file_name"] == "Northlight_Seed_Deck.pdf"
    assert body["claim_count"] == 0
    deck = DeckCheck.objects.get()
    assert deck.user == user
    assert deck.file.name.startswith(f"deck_checks/{user.id}/")
    assert "Northlight" not in deck.file.name  # client filename is not used on disk


@pytest.mark.parametrize(
    ("name", "content"),
    [("deck.pdf", b"not a pdf at all"), ("deck.docx", b"%PDF-1.4 sneaky")],
)
def test_rejects_non_pdf(auth_client, name, content):
    response = auth_client.post(LIST_URL, {"file": pdf_upload(name, content)}, format="multipart")

    assert response.status_code == 400
    assert "file" in response.json()["error"]["details"]
    assert not DeckCheck.objects.exists()


def test_rejects_oversized_file(auth_client, settings):
    settings.DECK_CHECK_MAX_UPLOAD_MB = 0

    response = auth_client.post(LIST_URL, {"file": pdf_upload()}, format="multipart")

    assert response.status_code == 400


def test_detail_includes_claims_in_slide_order(auth_client, user):
    deck = DeckCheckFactory(user=user)
    DeckClaimFactory(deck_check=deck, slide=5, claim="Team of 12")
    DeckClaimFactory(deck_check=deck, slide=2, claim="Founded 2023")

    response = auth_client.get(detail_url(deck))

    assert response.status_code == 200
    assert [c["slide"] for c in response.json()["claims"]] == [2, 5]
    assert response.json()["claim_count"] == 2


def test_deck_checks_are_private(auth_client):
    other = DeckCheckFactory()

    assert auth_client.get(LIST_URL).json()["results"] == []
    assert auth_client.get(detail_url(other)).status_code == 404
    assert auth_client.delete(detail_url(other)).status_code == 404


def test_delete_removes_row_and_file(auth_client, user, media_root, django_capture_on_commit_callbacks):
    deck = DeckCheckFactory(user=user)
    path = media_root / deck.file.name
    assert path.exists()

    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.delete(detail_url(deck))

    assert response.status_code == 204
    assert not DeckCheck.objects.exists()
    assert not path.exists()
