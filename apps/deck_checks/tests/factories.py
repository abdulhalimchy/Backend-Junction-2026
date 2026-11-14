import factory
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.tests.factories import UserFactory
from apps.deck_checks.models import DeckCheck, DeckClaim

PDF_BYTES = b"%PDF-1.4\n%fake test deck\n%%EOF\n"


def pdf_upload(name="Northlight_Seed_Deck.pdf", content=PDF_BYTES):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


class DeckCheckFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DeckCheck

    user = factory.SubFactory(UserFactory)
    file = factory.LazyFunction(pdf_upload)
    file_name = "Northlight_Seed_Deck.pdf"
    file_size = len(PDF_BYTES)


class DeckClaimFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DeckClaim

    deck_check = factory.SubFactory(DeckCheckFactory)
    slide = factory.Sequence(lambda n: n + 1)
    claim = "Raised EUR 1.2M pre-seed in 2024"
    category = DeckClaim.Category.FUNDING
    status = DeckClaim.Status.CONFIRMED
