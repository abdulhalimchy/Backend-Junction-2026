from django.contrib import admin

from .models import DismissedSuggestion, PortfolioCompany


@admin.register(PortfolioCompany)
class PortfolioCompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "domain",
        "user",
        "status",
        "origin",
        "is_draft",
        "enrichment_status",
        "created_at",
    )
    list_filter = ("status", "origin", "is_draft", "enrichment_status")
    search_fields = ("name", "domain", "user__email", "cala_company_id")
    list_select_related = ("user",)
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "enriched_at")


@admin.register(DismissedSuggestion)
class DismissedSuggestionAdmin(admin.ModelAdmin):
    list_display = ("name", "cala_company_id", "user", "created_at")
    raw_id_fields = ("user",)
