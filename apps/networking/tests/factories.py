import datetime

import factory

from apps.accounts.tests.factories import UserFactory
from apps.networking.models import MetEntry


class MetEntryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MetEntry

    user = factory.SubFactory(UserFactory)
    cala_person_id = factory.Sequence(lambda n: f"person-{n}")
    person_name = factory.Faker("name")
    met_on = factory.LazyFunction(datetime.date.today)
    where = "Coffee, Helsinki"
    note = ""
