import datetime

import factory

from apps.accounts.tests.factories import UserFactory
from apps.alerts.models import Alert


class AlertFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Alert

    user = factory.SubFactory(UserFactory)
    kind = Alert.Kind.COMPETITOR_ROUND
    title = factory.Sequence(lambda n: f"Alert {n}")
    body = "A competitor raised a seed round."
    href = "/company/tidewise"
    occurred_on = factory.LazyFunction(datetime.date.today)
