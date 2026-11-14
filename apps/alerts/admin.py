from django.contrib import admin

from .models import Alert, EntitySnapshot


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "user", "reason", "occurred_on", "read_at")
    list_filter = ("kind", "event")
    search_fields = ("title", "user__email")
    list_select_related = ("user", "event")
    raw_id_fields = ("user",)


@admin.register(EntitySnapshot)
class EntitySnapshotAdmin(admin.ModelAdmin):
    list_display = ("name", "entity_type", "cala_id", "checked_at")
    search_fields = ("name", "cala_id")
    readonly_fields = ("facts", "checked_at")
