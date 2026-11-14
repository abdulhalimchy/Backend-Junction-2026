from django.contrib import admin

from .models import Alert


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "user", "occurred_on", "read_at")
    list_filter = ("kind", "event")
    search_fields = ("title", "user__email")
    list_select_related = ("user", "event")
    raw_id_fields = ("user",)
