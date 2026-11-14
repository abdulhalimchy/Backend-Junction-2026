from django.conf import settings
from rest_framework import serializers

from apps.core.serializers import SourceSerializer
from apps.deck_checks.models import DeckCheck, DeckClaim


class DeckClaimSerializer(serializers.ModelSerializer):
    sources = SourceSerializer(many=True, read_only=True)

    class Meta:
        model = DeckClaim
        fields = ("id", "slide", "claim", "category", "status", "on_record", "sources", "basis")


class DeckCheckSerializer(serializers.ModelSerializer):
    """Summary used in lists. The file itself is never exposed by URL."""

    claim_count = serializers.SerializerMethodField()

    class Meta:
        model = DeckCheck
        fields = (
            "id",
            "file_name",
            "file_size",
            "cala_company_id",
            "company_name",
            "pages",
            "status",
            "error",
            "claim_count",
            "created_at",
            "checked_at",
        )
        read_only_fields = fields

    def get_claim_count(self, obj) -> int:
        # Annotated by the viewset's queryset; fall back to a query for fresh instances.
        annotated = getattr(obj, "claim_count", None)
        return annotated if annotated is not None else obj.claims.count()


class DeckCheckDetailSerializer(DeckCheckSerializer):
    claims = DeckClaimSerializer(many=True, read_only=True)

    class Meta(DeckCheckSerializer.Meta):
        fields = (*DeckCheckSerializer.Meta.fields, "claims")
        read_only_fields = fields


class DeckCheckUploadSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="PDF pitch deck.")
    cala_company_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    company_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_file(self, file):
        max_bytes = settings.DECK_CHECK_MAX_UPLOAD_MB * 1024 * 1024
        if file.size > max_bytes:
            raise serializers.ValidationError(f"File is larger than {settings.DECK_CHECK_MAX_UPLOAD_MB} MB.")
        if not file.name.lower().endswith(".pdf"):
            raise serializers.ValidationError("Only PDF files are accepted.")
        # Check the content, not just the name.
        header = file.read(5)
        file.seek(0)
        if header != b"%PDF-":
            raise serializers.ValidationError("File is not a valid PDF.")
        return file
