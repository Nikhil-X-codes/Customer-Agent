"""Enterprise identity and HTTP failure boundaries."""

import json
import io
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from support_agent.auth import issue_token, verify_token
from support_agent.cli import build_handler, configured_identity
from support_agent.handoff import HandoffQueue
from support_agent.http_guard import LimitedHTTPServer, RequestLimiter
from support_agent.identity import OIDCVerifier
from test_helpers import test_agent


class SigningKey:
    def __init__(self, key):
        self.key = key


class LocalJWKS:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, token):
        return SigningKey(self.key)


class FailingAgent:
    domain = "support"
    knowledge = None

    class Data:
        def ready(self):
            raise OSError("source unavailable")

    data = Data()

    def answer(self, question, customer_id=None, order_id=None):
        raise RuntimeError("internal detail must not leak")


class ProductionBoundaryTests(unittest.TestCase):
    def test_signed_tokens_have_bounded_subject_and_lifetime(self):
        with self.assertRaises(ValueError):
            issue_token("staff", "s" * 32, 86_401)
        with self.assertRaises(ValueError):
            issue_token("x" * 129, "s" * 32)
        self.assertEqual(verify_token(issue_token("staff", "s" * 32), "s" * 32), "staff")

    def test_worker_limit_rejects_excess_connection(self):
        entered = threading.Event()
        release = threading.Event()

        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                entered.set()
                release.wait(timeout=3)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = LimitedHTTPServer(("127.0.0.1", 0), SlowHandler, max_workers=1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        first = threading.Thread(target=lambda: urlopen(
            f"http://127.0.0.1:{server.server_port}/", timeout=4).close(), daemon=True)
        first.start()
        self.assertTrue(entered.wait(timeout=2))
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=2)
            self.assertEqual(error.exception.code, 503)
        finally:
            release.set()
            first.join(timeout=3)

    def test_oidc_checks_signature_issuer_audience_expiry_and_role(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        verifier = OIDCVerifier("https://id.example.test", "customer-agent",
                                "https://id.example.test/.well-known/jwks.json",
                                required_role="operations")
        verifier._client = LocalJWKS(private.public_key())
        now = int(time.time())
        claims = {"sub": "staff-123", "iat": now, "exp": now + 300,
                  "iss": "https://id.example.test", "aud": "customer-agent",
                  "roles": ["operations"]}
        token = jwt.encode(claims, private, algorithm="RS256")
        self.assertEqual(verifier.verify(token), "staff-123")
        for change in ({"aud": "wrong"}, {"iss": "https://other.test"},
                       {"exp": now - 1}, {"roles": ["customer"]}):
            with self.subTest(change=change):
                self.assertIsNone(verifier.verify(jwt.encode({**claims, **change}, private,
                                                       algorithm="RS256")))
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assertIsNone(verifier.verify(jwt.encode(claims, other, algorithm="RS256")))
        self.assertIsNone(verifier.verify("not-a-token"))

    def test_supply_oidc_requires_staff_role_configuration(self):
        settings = {"AUTH_MODE": "oidc", "OIDC_ISSUER": "https://id.example.test",
                    "OIDC_AUDIENCE": "customer-agent",
                    "OIDC_JWKS_URL": "https://id.example.test/jwks"}
        with patch.dict("os.environ", settings, clear=True):
            with self.assertRaisesRegex(ValueError, "OIDC_REQUIRED_ROLE"):
                configured_identity("", "supply_chain")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            OIDCVerifier("http://id.example.test", "customer-agent",
                         "https://id.example.test/jwks")

    def test_oidc_jwks_fetch_is_bounded_and_rejects_redirects(self):
        verifier = OIDCVerifier("https://id.example.test", "customer-agent",
                                "https://id.example.test/jwks")
        verifier._client.jwk_set_cache = None

        class Opener:
            def __init__(self, body):
                self.body = body
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                return io.BytesIO(self.body)

        opener = Opener(b'{"keys": []}')
        with patch("support_agent.identity.build_opener", return_value=opener) as factory:
            self.assertEqual(verifier._client.fetch_data(), {"keys": []})
        self.assertEqual(opener.timeout, 4)
        self.assertEqual(opener.request.full_url, "https://id.example.test/jwks")
        self.assertEqual(type(factory.call_args.args[0]).__name__, "_NoRedirect")
        with patch("support_agent.identity.build_opener",
                   return_value=Opener(b"x" * 65_537)):
            with self.assertRaisesRegex(ValueError, "too large"):
                verifier._client.fetch_data()

    def test_limiter_and_safe_http_failure(self):
        with tempfile.TemporaryDirectory() as root:
            queue = HandoffQueue(Path(root) / "tickets.db")
            limiter = RequestLimiter()
            self.assertTrue(limiter.allow("same", 1))
            self.assertFalse(limiter.allow("same", 1))
            secret = "a" * 32
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(
                FailingAgent(), secret, queue, chat_limit=1))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_port}"
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/ready", timeout=2)
            self.assertEqual(error.exception.code, 503)
            token = issue_token("customer-123", secret)
            request = Request(base + "/chat", data=json.dumps({"question": "status?"}).encode(),
                              headers={"Content-Type": "application/json",
                                       "Authorization": "Bearer " + token})
            with urlopen(request, timeout=2) as response:
                result = json.load(response)
                self.assertTrue(response.headers["X-Request-ID"])
            self.assertEqual(result["reason"], "agent_unavailable")
            self.assertEqual(result["outcome"], "handoff")
            self.assertIn("ticket_id", result)
            self.assertNotIn("internal detail", json.dumps(result))
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, 429)


if __name__ == "__main__":
    unittest.main()
