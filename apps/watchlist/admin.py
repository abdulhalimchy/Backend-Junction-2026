from django.contrib import admin, messages

from apps.alerts import scan
from apps.alerts.models import ScanRun
from apps.core.cala import CalaError

from .models import WatchItem


@admin.register(WatchItem)
class WatchItemAdmin(admin.ModelAdmin):
    list_display = ("name", "entity_type", "tag", "origin", "user", "created_at")
    list_filter = ("entity_type", "tag", "origin")
    search_fields = ("name", "cala_id", "user__email")
    list_select_related = ("user",)
    raw_id_fields = ("user",)
    actions = ("scan_for_changes",)

    @admin.action(description="Scan selected entities for changes (creates alerts)")
    def scan_for_changes(self, request, queryset):
        pairs = sorted(set(queryset.values_list("entity_type", "cala_id")))
        try:
            _, result = scan.run(pairs, trigger=ScanRun.Trigger.COMMAND)
        except CalaError as exc:
            self.message_user(request, f"Scan failed: {exc}", messages.ERROR)
            return
        self.message_user(
            request,
            f"Scanned {result.entities} entities, created {result.alerts_created} alerts"
            f" ({len(result.skipped)} demo ids skipped, {len(result.errors)} errors).",
        )
