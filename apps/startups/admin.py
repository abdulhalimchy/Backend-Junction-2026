from django.contrib import admin

from .models import Startup


@admin.register(Startup)
class StartupAdmin(admin.ModelAdmin):
    list_display = ("name", "domain", "user", "is_draft", "enrichment_status", "created_at")
    list_filter = ("is_draft", "enrichment_status")
    search_fields = ("name", "domain", "user__email", "cala_company_id")
    list_select_related = ("user",)
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "enriched_at")
