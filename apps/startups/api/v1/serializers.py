import re

from rest_framework import serializers

from apps.startups.enrichment import normalise_domain
from apps.startups.models import Startup, StartupAnalysis

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def clean_domain(value: str) -> str:
    domain = normalise_domain(value)
    if domain and not DOMAIN_RE.match(domain):
        raise serializers.ValidationError("Enter a domain like northlight.ai.")
    return domain


class StartupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Startup
        fields = (
            "id",
            "name",
            "domain",
            "description",
            "founded",
            "headquarters",
            "employees",
            "stage",
            "industry",
            "cala_company_id",
            "sources",
            "is_draft",
            "enrichment_status",
            "enrichment_error",
            "enrichment_note",
            "suggestions",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "cala_company_id",
            "sources",
            "enrichment_status",
            "enrichment_error",
            "enrichment_note",
            "suggestions",
            "created_at",
            "updated_at",
        )

    def validate_domain(self, value):
        return clean_domain(value)

    def validate_founded(self, value):
        if value and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", value):
            raise serializers.ValidationError("Use YYYY, YYYY-MM or YYYY-MM-DD.")
        return value

    def validate_is_draft(self, value):
        if value and (self.instance is None or not self.instance.is_draft):
            raise serializers.ValidationError("Only a lookup creates drafts.")
        return value

    def validate(self, attrs):
        name = attrs.get("name", self.instance.name if self.instance else "")
        is_draft = attrs.get("is_draft", self.instance.is_draft if self.instance else False)
        if not is_draft and not (name or "").strip():
            raise serializers.ValidationError({"name": "Give the startup a name."})
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        return attrs


class StartupLookupSerializer(serializers.Serializer):
    """A domain, a Cala company picked from search/, or both."""

    domain = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    cala_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate_domain(self, value):
        return clean_domain(value)

    def validate(self, attrs):
        if not attrs["domain"] and not attrs["cala_id"]:
            raise serializers.ValidationError(
                {"domain": "Enter the company's domain or pick it from the search."}
            )
        attrs["cala_id"] = str(attrs["cala_id"]) if attrs["cala_id"] else ""
        return attrs


class StartupSearchSerializer(serializers.Serializer):
    q = serializers.CharField(min_length=2, max_length=100, trim_whitespace=True)


class CalaCandidateSerializer(serializers.Serializer):
    """A company in Cala the user can pick when adding a startup by name."""

    id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    domain = serializers.CharField(allow_blank=True, help_text="Website on record in Cala; empty if none.")
    headquarters = serializers.CharField(allow_blank=True)
    founded = serializers.CharField(allow_blank=True, help_text="YYYY, or empty.")


class StartupAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = StartupAnalysis
        fields = ("kind", "status", "data", "error", "started_at", "finished_at")
        read_only_fields = fields
