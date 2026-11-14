import factory

from apps.accounts.tests.factories import UserFactory
from apps.watchlist.models import WatchItem


class WatchItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WatchItem

    user = factory.SubFactory(UserFactory)
    entity_type = WatchItem.EntityType.COMPANY
    cala_id = factory.Sequence(lambda n: f"company-{n}")
    name = factory.Sequence(lambda n: f"Company {n}")
