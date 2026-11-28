import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from support_agent.cli import make_agent
from support_agent.data import HttpStatusProvider, StatusError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.payload


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload

    def open(self, request, timeout):
        return FakeResponse(self.payload)


class StatusAdapterTests(unittest.TestCase):
    def test_https_is_required(self):
        with self.assertRaises(ValueError):
            HttpStatusProvider("http://example.com", "token")

    def test_order_id_mismatch_fails_closed(self):
        payload = {"order_id": "ORD-OTHER", "state": "shipped", "detail": "",
                   "observed_at": datetime.now(timezone.utc).isoformat()}
        with patch("support_agent.data.build_opener", return_value=FakeOpener(json.dumps(payload).encode())):
            with self.assertRaises(StatusError):
                HttpStatusProvider("https://example.com", "token").get("ORD-1001")

    def test_oversized_response_fails_closed(self):
        with patch("support_agent.data.build_opener", return_value=FakeOpener(b"x" * 16385)):
            with self.assertRaises(StatusError):
                HttpStatusProvider("https://example.com", "token").get("ORD-1001")

    def test_malformed_status_field_fails_closed(self):
        payload = {"order_id": "ORD-1001", "state": ["shipped"],
                   "observed_at": datetime.now(timezone.utc).isoformat()}
        with patch("support_agent.data.build_opener", return_value=FakeOpener(json.dumps(payload).encode())):
            with self.assertRaises(StatusError):
                HttpStatusProvider("https://example.com", "token").get("ORD-1001")

    def test_production_requires_real_sources(self):
        with patch.dict("os.environ", {"APP_MODE": "production"}, clear=True):
            with self.assertRaises(ValueError):
                make_agent()

    def test_production_requires_catalog_database(self):
        with patch.dict("os.environ", {"APP_MODE": "production", "AGENT_DOMAIN": "support",
                                    "ORDER_DB_PATH": "/tmp/example.db",
                                    "STATUS_API_URL": "https://status.example.com",
                                    "STATUS_API_TOKEN": "test"}, clear=True):
            with self.assertRaisesRegex(ValueError, "CATALOG_DB_PATH"):
                make_agent()

    def test_ai_mode_requires_key(self):
        with patch.dict("os.environ", {"AI_MODE": "openai"}, clear=True):
            with self.assertRaises(ValueError):
                make_agent()

    def test_groq_mode_requires_key(self):
        with patch.dict("os.environ", {"AI_MODE": "groq"}, clear=True):
            with self.assertRaises(ValueError):
                make_agent()


if __name__ == "__main__":
    unittest.main()
