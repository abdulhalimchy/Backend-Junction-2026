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
    domain = serializers.CharField(max_length=255)

    def validate_domain(self, value):
        domain = clean_domain(value)
        if not domain:
            raise serializers.ValidationError("Enter the company's domain.")
        return domain


class StartupAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = StartupAnalysis
        fields = ("kind", "status", "data", "error", "started_at", "finished_at")
        read_only_fields = fields
