import datetime

import factory

from apps.events.models import Event, EventAttendance


class EventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Event
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"event-{n}")
    name = factory.Sequence(lambda n: f"Event {n}")
    city = "Helsinki, Finland"
    starts_on = datetime.date(2026, 11, 18)
    ends_on = datetime.date(2026, 11, 19)


class EventAttendanceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventAttendance

    event = factory.SubFactory(EventFactory)
    entity_type = EventAttendance.EntityType.COMPANY
    cala_entity_id = factory.Sequence(lambda n: f"entity-{n}")
    name = factory.Sequence(lambda n: f"Company {n}")
    joined_on = factory.LazyFunction(datetime.date.today)
