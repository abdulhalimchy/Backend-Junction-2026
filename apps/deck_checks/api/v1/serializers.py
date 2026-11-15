from django.conf import settings
from rest_framework import serializers

from apps.core.serializers import SourceSerializer
from apps.deck_checks.models import DeckCheck, DeckClaim
from apps.startups.models import Startup


class DeckClaimSerializer(serializers.ModelSerializer):
    sources = SourceSerializer(many=True, read_only=True)

    class Meta:
        model = DeckClaim
        fields = ("id", "slide", "claim", "category", "status", "on_record", "sources", "basis")


class DeckCheckSerializer(serializers.ModelSerializer):
    """Summary used in lists. The file itself is never exposed by URL."""

    claim_count = serializers.SerializerMethodField()
    confirmed_count = serializers.SerializerMethodField()
    differs_count = serializers.SerializerMethodField()
    cant_verify_count = serializers.SerializerMethodField()

    class Meta:
        model = DeckCheck
        fields = (
            "id",
            "file_name",
            "file_size",
            "cala_company_id",
            "company_name",
            "startup",
            "pages",
            "status",
            "error",
            "claim_count",
            "confirmed_count",
            "differs_count",
            "cant_verify_count",
            "created_at",
            "checked_at",
        )
        read_only_fields = fields

    def get_claim_count(self, obj) -> int:
        # Annotated by the viewset's queryset; fall back to a query for fresh instances.
        annotated = getattr(obj, "claim_count", None)
        return annotated if annotated is not None else obj.claims.count()

    def _status_count(self, obj, value: str) -> int:
        annotated = getattr(obj, f"{value}_count", None)
        return annotated if annotated is not None else obj.claims.filter(status=value).count()

    def get_confirmed_count(self, obj) -> int:
        return self._status_count(obj, DeckClaim.Status.CONFIRMED)

    def get_differs_count(self, obj) -> int:
        return self._status_count(obj, DeckClaim.Status.DIFFERS)

    def get_cant_verify_count(self, obj) -> int:
        return self._status_count(obj, DeckClaim.Status.CANT_VERIFY)


class DeckCheckDetailSerializer(DeckCheckSerializer):
    claims = DeckClaimSerializer(many=True, read_only=True)

    class Meta(DeckCheckSerializer.Meta):
        fields = (*DeckCheckSerializer.Meta.fields, "claims")
        read_only_fields = fields


class DeckCheckUploadSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="PDF pitch deck.")
    cala_company_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    company_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    startup = serializers.PrimaryKeyRelatedField(
        queryset=Startup.objects.none(),  # narrowed to the uploader's own startups in __init__
        required=False,
        allow_null=True,
        default=None,
        help_text="One of your startups; the deck is listed under it.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            self.fields["startup"].queryset = Startup.objects.filter(user=request.user, is_draft=False)

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
