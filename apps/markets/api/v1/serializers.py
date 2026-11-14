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
