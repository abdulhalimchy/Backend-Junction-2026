from django.contrib import admin

from .models import DeckCheck, DeckClaim


class DeckClaimInline(admin.TabularInline):
    model = DeckClaim
    extra = 0


@admin.register(DeckCheck)
class DeckCheckAdmin(admin.ModelAdmin):
    list_display = ("file_name", "user", "status", "pages", "created_at", "checked_at")
    list_filter = ("status",)
    search_fields = ("file_name", "user__email", "cala_company_id")
    list_select_related = ("user",)
    raw_id_fields = ("user",)
    readonly_fields = ("file_size", "created_at", "updated_at")
    inlines = (DeckClaimInline,)
