"""DRF serializers — public contract for the summarizer API."""
from rest_framework import serializers


class HealthOutSerializer(serializers.Serializer):
    status = serializers.CharField()


class SummarizeInSerializer(serializers.Serializer):
    """Inbound text to summarize."""

    text = serializers.CharField(min_length=1, trim_whitespace=True)


class SummarizeOutSerializer(serializers.Serializer):
    """Summary returned to the caller."""

    summary = serializers.CharField()