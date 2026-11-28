"""HTTPS read-only adapter for an ERP/WMS/TMS supply gateway."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .supply import SupplyDataError, _fresh


MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class HttpSupplyStore:
    """Expected endpoints and schemas are documented in docs/REAL_DATA.md."""

    def __init__(self, base_url: str, token: str, timeout: float = 4.0,
                 max_sync_age: timedelta = timedelta(minutes=30)) -> None:
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment):
            raise ValueError("SUPPLY_API_URL must be a clean HTTPS base URL")
        if not token or token.startswith("replace-"):
            raise ValueError("SUPPLY_API_TOKEN must be a real read-only token")
        if not timedelta(0) < max_sync_age <= timedelta(days=1):
            raise ValueError("Supply sync age limit must be 1 minute to 24 hours")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.max_sync_age = max_sync_age

    def _assert_live_source(self) -> None:
        health = self._get("/health")
        source = health.get("source")
        if (health.get("status") != "ready" or not isinstance(source, str) or
                len(source.strip()) < 3 or
                any(term in source.lower() for term in ("demo", "mock", "fixture")) or
                not _fresh(health.get("synced_at"), self.max_sync_age)):
            raise SupplyDataError("Supply API source is missing or stale")

    def _get(self, path: str, *, missing_ok: bool = False) -> dict | None:
        request = Request(self.base_url + path, method="GET",
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Accept": "application/json"})
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            raise SupplyDataError("Supply API unavailable") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SupplyDataError("Supply API unavailable") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SupplyDataError("Supply API response too large")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SupplyDataError("Supply API returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise SupplyDataError("Supply API returned invalid data")
        return value

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9-]{2,64}", value):
            raise SupplyDataError("Invalid supply record identifier")
        return quote(value, safe="")

    @staticmethod
    def _record(value: dict, fields: set[str], identifier: str, key: str) -> dict:
        if not fields.issubset(value) or value[key] != identifier:
            raise SupplyDataError("Supply API record mismatch")
        return value

    def inventory(self, sku: str) -> dict | None:
        self._assert_live_source()
        value = self._get(f"/inventory/{self._id(sku)}", missing_ok=True)
        return (self._record(value, {"sku", "product_name", "location", "on_hand",
                                     "reserved", "reorder_point", "observed_at"}, sku, "sku")
                if value is not None else None)

    def inventory_all(self) -> list[dict]:
        self._assert_live_source()
        value = self._get("/inventory?limit=5001")
        items = value.get("items")
        if value.get("complete") is not True or not isinstance(items, list) or len(items) > 5000:
            raise SupplyDataError("Supply API inventory snapshot is incomplete")
        if any(not isinstance(item, dict) or
               not {"sku", "product_name", "location", "on_hand", "reserved",
                    "reorder_point", "observed_at"}.issubset(item) for item in items):
            raise SupplyDataError("Supply API inventory row is invalid")
        return items

    def purchase_order(self, identifier: str) -> dict | None:
        self._assert_live_source()
        value = self._get(f"/purchase-orders/{self._id(identifier)}", missing_ok=True)
        return (self._record(value, {"id", "supplier", "sku", "quantity", "state",
                                     "expected_at", "observed_at"}, identifier, "id")
                if value is not None else None)

    def shipment(self, identifier: str) -> dict | None:
        self._assert_live_source()
        value = self._get(f"/shipments/{self._id(identifier)}", missing_ok=True)
        return (self._record(value, {"id", "purchase_order_id", "carrier", "state",
                                     "eta", "observed_at"}, identifier, "id")
                if value is not None else None)

    def ready(self) -> bool:
        try:
            self._assert_live_source()
            return True
        except SupplyDataError:
            return False
