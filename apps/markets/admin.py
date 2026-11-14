from django.contrib import admin

from .models import MarketAnalysis


@admin.register(MarketAnalysis)
class MarketAnalysisAdmin(admin.ModelAdmin):
    list_display = ("sector", "region", "user", "status", "runs", "updated_at")
    list_filter = ("status", "region")
    search_fields = ("sector", "typed", "user__email")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "started_at", "finished_at")
