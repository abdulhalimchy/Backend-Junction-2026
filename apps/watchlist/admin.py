from django.contrib import admin

from .models import WatchItem


@admin.register(WatchItem)
class WatchItemAdmin(admin.ModelAdmin):
    list_display = ("name", "entity_type", "tag", "origin", "user", "created_at")
    list_filter = ("entity_type", "tag", "origin")
    search_fields = ("name", "cala_id", "user__email")
    list_select_related = ("user",)
    raw_id_fields = ("user",)
