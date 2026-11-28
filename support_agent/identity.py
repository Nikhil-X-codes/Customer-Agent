"""Identity verification boundary for the HTTP application."""

from __future__ import annotations

import json
from typing import Protocol
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit

from .auth import verify_token


class IdentityVerifier(Protocol):
    def verify(self, token: str) -> str | None: ...


class SignedTokenVerifier:
    """For a trusted company backend that issues this application's HMAC tokens."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 32 or secret.startswith("replace-"):
            raise ValueError("SESSION_SECRET must be a real random secret of at least 32 characters")
        self.secret = secret

    def verify(self, token: str) -> str | None:
        return verify_token(token, self.secret)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class OIDCVerifier:
    """Validate an RS256 access token against a company's OIDC JWKS endpoint."""

    def __init__(self, issuer: str, audience: str, jwks_url: str, *,
                 required_role: str | None = None, role_claim: str = "roles") -> None:
        for value, name in ((issuer, "OIDC_ISSUER"), (jwks_url, "OIDC_JWKS_URL")):
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                    parsed.password or parsed.query or parsed.fragment):
                raise ValueError(f"{name} must be a clean HTTPS URL")
        if not audience or not audience.strip():
            raise ValueError("OIDC_AUDIENCE is required")
        if required_role is not None and not required_role.strip():
            raise ValueError("OIDC_REQUIRED_ROLE must be nonblank")
        if not role_claim or not role_claim.strip():
            raise ValueError("OIDC_ROLE_CLAIM must be nonblank")
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError("OIDC requires pip install 'customer-agent[oidc]'") from exc
        self._jwt = jwt
        class StrictJWKClient(jwt.PyJWKClient):
            def fetch_data(self):
                request = Request(self.uri, headers={"Accept": "application/json"})
                with build_opener(_NoRedirect()).open(request, timeout=4) as response:
                    raw = response.read(65_537)
                if len(raw) > 65_536:
                    raise ValueError("JWKS response too large")
                data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
                    raise ValueError("Invalid JWKS response")
                if self.jwk_set_cache is not None:
                    self.jwk_set_cache.put(data)
                return data

        # Refresh the JWKS after five minutes so rotated keys are observed.
        self._client = StrictJWKClient(jwks_url, cache_keys=False,
                                       cache_jwk_set=True, lifespan=300, timeout=4)
        self.issuer, self.audience = issuer, audience
        self.required_role, self.role_claim = required_role, role_claim

    def verify(self, token: str) -> str | None:
        if not isinstance(token, str) or not token or len(token) > 8192:
            return None
        try:
            key = self._client.get_signing_key_from_jwt(token).key
            claims = self._jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.issuer,
                audience=self.audience, options={"require": ["sub", "exp", "iat"]})
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject.strip() or len(subject) > 128:
                return None
            if self.required_role:
                roles = claims.get(self.role_claim)
                if not isinstance(roles, list) or self.required_role not in roles:
                    return None
            return subject
        except (self._jwt.PyJWTError, OSError, TimeoutError, ValueError, TypeError):
            return None
