from rest_framework import serializers

MAX_FACTS = 80


class GuardRequestSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=500, trim_whitespace=True)
    known = serializers.ListField(
        child=serializers.CharField(max_length=120), required=False, default=list, max_length=500
    )


class GuardResponseSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["market", "company", "not_market"])
    reason = serializers.CharField()
    suggestions = serializers.ListField(child=serializers.CharField())
    canonical = serializers.CharField(allow_blank=True)
    tokens = serializers.DictField(child=serializers.IntegerField())


class SourceSerializer(serializers.Serializer):
    """A Cala source as the frontend holds it; passed through unchanged onto the claims that cite it."""

    name = serializers.CharField(allow_blank=True)
    document = serializers.CharField(allow_blank=True)
    date = serializers.CharField(allow_blank=True, required=False, default="")
    date_precision = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    source_rank = serializers.CharField(allow_blank=True, allow_null=True, required=False)


class FactSerializer(serializers.Serializer):
    id = serializers.RegexField(r"^[A-Z]\d{1,3}$")
    text = serializers.CharField(max_length=2000, trim_whitespace=False)
    sources = SourceSerializer(many=True)


class SummaryRequestSerializer(serializers.Serializer):
    market = serializers.CharField(max_length=120)
    region = serializers.CharField(max_length=80)
    year = serializers.IntegerField(min_value=2000, max_value=2100)
    facts = FactSerializer(many=True, allow_empty=False, max_length=MAX_FACTS)


class OpenAnalysisSerializer(serializers.Serializer):
    """
    Open (or start) the analysis of free text: any wording, with or without a place. The LLM turns it into the
    Cala requests (planner.py). force runs it even when the text does not read as a market.
    """

    text = serializers.CharField(max_length=300, trim_whitespace=True)
    force = serializers.BooleanField(default=False)


class FindCompanySerializer(serializers.Serializer):
    """Free text naming a company ("orange telecom", "Spotify competitors in Europe")."""

    text = serializers.CharField(max_length=300, trim_whitespace=True)
    force = serializers.BooleanField(default=False)


class OpenCompanySerializer(serializers.Serializer):
    """The company the user picked from the candidates, and the text they searched (for the place)."""

    entity_id = serializers.RegexField(r"^[0-9a-fA-F-]{36}$")
    name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    text = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class MarketAnalysisListSerializer(serializers.Serializer):
    """One history row: enough to list it without the full report."""

    id = serializers.UUIDField()
    kind = serializers.CharField()
    entity_id = serializers.CharField()
    sector = serializers.CharField()
    typed = serializers.CharField()
    region = serializers.CharField()
    place = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    year = serializers.IntegerField()
    status = serializers.CharField()
    runs = serializers.IntegerField()
    error = serializers.CharField()
    headline = serializers.SerializerMethodField()
    player_count = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField(allow_null=True)

    def get_place(self, obj) -> str:
        return (obj.plan or {}).get("place") or obj.region

    def get_title(self, obj) -> str:
        return obj.title or (obj.plan or {}).get("title") or obj.sector

    def get_headline(self, obj) -> str:
        return (((obj.data or {}).get("summary") or {}).get("headline") or {}).get("text") or ""

    def get_player_count(self, obj) -> int:
        return len(((obj.data or {}).get("players") or {}).get("companies") or [])


class MarketAnalysisSerializer(MarketAnalysisListSerializer):
    data = serializers.JSONField()
    changes = serializers.JSONField()
    plan = serializers.JSONField()
    can_stop = serializers.SerializerMethodField()
    started_at = serializers.DateTimeField(allow_null=True)

    def get_can_stop(self, obj) -> bool:
        return obj.status == "loading"
