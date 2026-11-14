from django.contrib import admin

from .models import Event, EventAttendance


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "city", "starts_on", "ends_on", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "cala_event_id")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(EventAttendance)
class EventAttendanceAdmin(admin.ModelAdmin):
    list_display = ("name", "entity_type", "event", "joined_on")
    list_filter = ("event", "entity_type")
    search_fields = ("name", "cala_entity_id")
    list_select_related = ("event",)
