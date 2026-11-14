"""Read-side queries for events."""

from datetime import timedelta

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from .models import Event, EventAttendance, SideEventRSVP


def events_with_stats() -> QuerySet[Event]:
    week_ago = timezone.localdate() - timedelta(days=7)
    return (
        Event.objects.filter(is_active=True)
        .annotate(
            attendee_companies=Count(
                "attendances", filter=Q(attendances__entity_type=EventAttendance.EntityType.COMPANY)
            ),
            attendee_investors=Count(
                "attendances", filter=Q(attendances__entity_type=EventAttendance.EntityType.INVESTOR)
            ),
            new_this_week=Count("attendances", filter=Q(attendances__joined_on__gte=week_ago)),
        )
        .order_by("-starts_on")
    )  # Meta.ordering is dropped on aggregate queries


def side_event_rsvp_summary(event: Event, user) -> dict:
    """The user's own RSVPs for this event's side events, and anonymous counts for every side event."""
    rsvps = SideEventRSVP.objects.filter(event=event)
    mine = sorted(rsvps.filter(user=user).values_list("side_event_id", flat=True))
    counts = {
        row["side_event_id"]: row["n"]
        for row in rsvps.values("side_event_id").annotate(n=Count("id")).order_by()
    }
    return {"mine": mine, "counts": counts}
