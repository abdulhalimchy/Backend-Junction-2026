from django.contrib import admin

from .models import MetEntry


@admin.register(MetEntry)
class MetEntryAdmin(admin.ModelAdmin):
    list_display = ("person_name", "cala_person_id", "user", "met_on", "event")
    list_filter = ("event",)
    search_fields = ("person_name", "cala_person_id", "user__email", "where")
    list_select_related = ("user", "event")
    raw_id_fields = ("user",)
