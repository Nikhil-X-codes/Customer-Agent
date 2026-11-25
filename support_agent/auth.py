"""Small signed session token for a trusted storefront integration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(customer_id: str, secret: str, ttl_seconds: int = 3600) -> str:
    if not customer_id or not secret or ttl_seconds <= 0:
        raise ValueError("customer_id, secret, and positive TTL required")
    body = _encode(json.dumps({"sub": customer_id, "exp": int(time.time()) + ttl_seconds},
                              separators=(",", ":")).encode())
    signature = _encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_token(token: str, secret: str) -> str | None:
    if not secret or len(token) > 4096:
        return None
    try:
        body, signature = token.split(".")
        expected = _encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_decode(body))
        if not isinstance(payload.get("sub"), str) or not payload["sub"]:
            return None
        if not isinstance(payload.get("exp"), int) or payload["exp"] <= time.time():
            return None
        return payload["sub"]
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        return None
