"""Seed Slush 2026, the first event space. The alert scan matches Cala attendance to it by cala_event_id."""

import datetime

from django.db import migrations

SLUSH = {
    "slug": "slush-2026",
    "name": "Slush 2026",
    "city": "Helsinki",
    "starts_on": datetime.date(2026, 11, 18),
    "ends_on": datetime.date(2026, 11, 19),
    "cala_event_id": "bc844d56-6ab4-452b-96bf-2661e86e592b",
}


def seed(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    event, created = Event.objects.get_or_create(slug=SLUSH["slug"], defaults=SLUSH)
    if not created and not event.cala_event_id:
        event.cala_event_id = SLUSH["cala_event_id"]
        event.save(update_fields=["cala_event_id"])


class Migration(migrations.Migration):
    dependencies = [("events", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
