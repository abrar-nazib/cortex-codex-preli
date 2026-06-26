"""POST /summarize. Normalizer mocked with unittest.mock.patch — no network."""
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from tickets.summarize_client import SummarizerError


class SummarizeTest(APITestCase):
    def test_summarize_ok(self):
        with patch("tickets.views.call_summarize", return_value="short summary") as mock:
            r = self.client.post("/summarize", {"text": "hello world"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json(), {"summary": "short summary"})
        mock.assert_called_once_with("hello world")

    def test_summarize_empty_rejected(self):
        r = self.client.post("/summarize", {"text": ""}, format="json")
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_summarize_missing_text_rejected(self):
        r = self.client.post("/summarize", {}, format="json")
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_summarize_normalizer_failure_returns_502(self):
        with patch("tickets.views.call_summarize",
                   side_effect=SummarizerError("normalizer 503: boom")):
            r = self.client.post("/summarize", {"text": "some text"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn("boom", r.json()["detail"])