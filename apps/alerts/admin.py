from django.contrib import admin

from .models import Alert, EntitySnapshot, ScanRun


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "user", "reason", "occurred_on", "read_at")
    list_filter = ("kind", "event", "entity_type")
    search_fields = ("title", "user__email", "cala_id")
    list_select_related = ("user", "event")
    raw_id_fields = ("user",)


@admin.register(EntitySnapshot)
class EntitySnapshotAdmin(admin.ModelAdmin):
    list_display = ("name", "entity_type", "cala_id", "checked_at")
    search_fields = ("name", "cala_id")
    readonly_fields = ("facts", "checked_at")


@admin.register(ScanRun)
class ScanRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "trigger", "status", "user", "entities", "alerts_created", "skipped")
    list_filter = ("trigger", "status")
    readonly_fields = ("errors",)
    raw_id_fields = ("user",)
