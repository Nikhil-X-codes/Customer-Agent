"""Store data access. Production adapters can implement the same interfaces."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True)
class Status:
    order_id: str
    state: str
    detail: str
    observed_at: datetime
    source: str


class StatusError(Exception):
    pass


class FixtureStatusProvider:
    """Local demonstration only. Never represents a live carrier feed."""

    def __init__(self, path: Path = DATA_DIR / "statuses.json") -> None:
        self._statuses = load_json(path)

    def get(self, order_id: str) -> Status:
        row = self._statuses.get(order_id)
        if not row:
            raise StatusError("No status record")
        return Status(order_id, row["state"], row["detail"],
                      datetime.now(timezone.utc), "demo fixture")


class HttpStatusProvider:
    """GET {base_url}/orders/{id}/status; expects state, detail, observed_at."""

    def __init__(self, base_url: str, token: str, timeout: float = 3.0) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Status API must use HTTPS")
        if not token:
            raise ValueError("Status API token is required")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def get(self, order_id: str) -> Status:
        url = f"{self.base_url}/orders/{quote(order_id, safe='')}/status"
        request = Request(url, headers={"Authorization": f"Bearer {self.token}",
                                        "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
            observed = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("observed_at must include timezone")
            if payload["order_id"] != order_id:
                raise ValueError("Order ID mismatch")
            return Status(order_id, payload["state"], payload.get("detail", ""),
                          observed, "live status API")
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            raise StatusError("Status service unavailable or invalid") from exc


class StoreData:
    def __init__(self, directory: Path = DATA_DIR) -> None:
        self.policies = load_json(directory / "policies.json")
        self.products = load_json(directory / "products.json")
        self.orders = load_json(directory / "orders.json")

    def order_for_customer(self, order_id: str, customer_id: str) -> dict | None:
        # Caller identity must come from a verified session, never the question text.
        return next((row for row in self.orders if row["id"] == order_id
                     and row["customer_id"] == customer_id), None)
