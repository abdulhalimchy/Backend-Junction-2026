import re

from rest_framework import serializers

from apps.portfolio.models import PortfolioAnalysis, PortfolioCompany
from apps.startups.api.v1.serializers import clean_domain

DATE_RE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")


def clean_fuzzy_date(value: str) -> str:
    if value and not DATE_RE.fullmatch(value):
        raise serializers.ValidationError("Use YYYY, YYYY-MM or YYYY-MM-DD.")
    return value


class PortfolioCompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = PortfolioCompany
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
            "invested_amount",
            "currency",
            "entry_round",
            "invested_on",
            "ownership",
            "board_seat",
            "status",
            "notes",
            "origin",
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
            "origin",
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
        return clean_fuzzy_date(value)

    def validate_invested_on(self, value):
        return clean_fuzzy_date(value)

    def validate_currency(self, value):
        value = (value or "").strip().upper()
        if value and not re.fullmatch(r"[A-Z]{3}", value):
            raise serializers.ValidationError("Use a 3-letter currency code, e.g. EUR.")
        return value

    def validate_invested_amount(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("The amount cannot be negative.")
        return value

    def validate_is_draft(self, value):
        if value and (self.instance is None or not self.instance.is_draft):
            raise serializers.ValidationError("Only a lookup creates drafts.")
        return value

    def validate(self, attrs):
        inst = self.instance
        name = attrs.get("name", inst.name if inst else "")
        is_draft = attrs.get("is_draft", inst.is_draft if inst else False)
        if not is_draft and not (name or "").strip():
            raise serializers.ValidationError({"name": "Give the company a name."})
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        # Saving a draft whose lookup found a company already in the portfolio.
        if inst and inst.is_draft and not is_draft and inst.cala_company_id:
            dupe = PortfolioCompany.objects.filter(
                user=inst.user, is_draft=False, cala_company_id=inst.cala_company_id
            ).first()
            if dupe:
                raise serializers.ValidationError({"name": f"{dupe.name} is already in your portfolio."})
        return attrs


class PortfolioLookupSerializer(serializers.Serializer):
    """A domain, a Cala company picked from search (or a suggestion), or both.

    `quick`: save it straight away (one-click add of a suggestion); the lookup fills in the rest.
    """

    domain = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    cala_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    quick = serializers.BooleanField(required=False, default=False)

    def validate_domain(self, value):
        return clean_domain(value)

    def validate(self, attrs):
        if not attrs["domain"] and not attrs["cala_id"]:
            raise serializers.ValidationError(
                {"domain": "Enter the company's domain or pick it from the search."}
            )
        attrs["cala_id"] = str(attrs["cala_id"]) if attrs["cala_id"] else ""
        if attrs["quick"] and not (attrs["cala_id"] and attrs["name"].strip()):
            raise serializers.ValidationError(
                {"quick": "A one-click add needs the Cala company and its name."}
            )
        attrs["name"] = attrs["name"].strip()
        return attrs


class FundSerializer(serializers.Serializer):
    cala_id = serializers.UUIDField(allow_null=True)


class DismissSerializer(serializers.Serializer):
    cala_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class SuggestionSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    first_reported = serializers.CharField(allow_null=True)
    sources = serializers.ListField(child=serializers.DictField())


class SuggestionsSerializer(serializers.Serializer):
    fund = serializers.DictField(allow_null=True)
    results = SuggestionSerializer(many=True)
    capped = serializers.BooleanField(required=False)


class PortfolioAnalysisSerializer(serializers.ModelSerializer):
    company = serializers.UUIDField(source="company_id", read_only=True)

    class Meta:
        model = PortfolioAnalysis
        fields = ("company", "kind", "status", "data", "error", "started_at", "finished_at")
        read_only_fields = fields


class AnalysesRefreshSerializer(serializers.Serializer):
    kinds = serializers.ListField(
        child=serializers.ChoiceField(choices=PortfolioAnalysis.Kind.choices),
        required=False,
        default=list(PortfolioAnalysis.Kind.values),
        allow_empty=False,
    )
    company = serializers.UUIDField(required=False, allow_null=True, default=None)
