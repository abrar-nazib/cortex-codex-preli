"""Views: GET /health, POST /summarize."""
from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import HealthOutSerializer, SummarizeInSerializer, SummarizeOutSerializer
from .summarize_client import SummarizerError, call_summarize

log = logging.getLogger("backend")


class HealthView(APIView):
    """Service health. Public, no auth. Must respond within 10s."""

    @extend_schema(responses=HealthOutSerializer)
    def get(self, request):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)


class SummarizeView(APIView):
    """Summarize one piece of text via the normalizer. Public, no auth."""

    @extend_schema(
        request=SummarizeInSerializer,
        responses=SummarizeOutSerializer,
        examples=[
            OpenApiExample(
                "Summarize",
                value={"text": "Paste any text here to get a concise summary."},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = SummarizeInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        text = serializer.validated_data["text"]

        log.info("POST /summarize len=%d", len(text))
        try:
            summary = call_summarize(text)
        except SummarizerError as exc:
            log.warning("summarize FAILED: %s", exc)
            return Response(
                {"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )

        log.info("summarize OK summary_len=%d", len(summary))
        return Response({"summary": summary}, status=status.HTTP_200_OK)