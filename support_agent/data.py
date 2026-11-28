"""Store data access. Production adapters can implement the same interfaces."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


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


class OrderLookupError(Exception):
    pass


class CatalogError(Exception):
    pass


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
            # Do not forward the bearer token to a redirect destination.
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(16385)
                if len(raw) > 16384:
                    raise ValueError("Status response too large")
                payload = json.loads(raw)
            observed = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("observed_at must include timezone")
            if payload["order_id"] != order_id:
                raise ValueError("Order ID mismatch")
            if not isinstance(payload["state"], str) or not isinstance(payload.get("detail", ""), str):
                raise ValueError("Invalid status fields")
            return Status(order_id, payload["state"], payload.get("detail", ""),
                          observed, "live status API")
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError,
                UnicodeDecodeError) as exc:
            raise StatusError("Status service unavailable or invalid") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class StoreData:
    def __init__(self, directory: Path) -> None:
        self.policies = load_json(directory / "policies.json")
        self.products = load_json(directory / "products.json")
        self.orders = load_json(directory / "orders.json")

    def order_for_customer(self, order_id: str, customer_id: str) -> dict | None:
        # Caller identity must come from a verified session, never the question text.
        return next((row for row in self.orders if row["id"] == order_id
                     and row["customer_id"] == customer_id), None)

    def catalog(self) -> tuple[list[dict], list[dict]]:
        return self.policies, self.products

    def ready(self) -> bool:
        return True


class SqliteStoreData(StoreData):
    """Read-only order lookup; policy and catalog remain versioned JSON."""

    def __init__(self, database: Path, directory: Path | None = None) -> None:
        self.policies = load_json(directory / "policies.json") if directory else []
        self.products = load_json(directory / "products.json") if directory else []
        self.database = database.resolve()
        if not self.database.is_file():
            raise FileNotFoundError(self.database)

    def order_for_customer(self, order_id: str, customer_id: str) -> dict | None:
        db_uri = f"file:{quote(str(self.database), safe='/')}?mode=ro"
        try:
            with sqlite3.connect(db_uri, uri=True, timeout=2) as db:
                row = db.execute("SELECT id, customer_id, state FROM orders "
                                 "WHERE id = ? AND customer_id = ?", (order_id, customer_id)).fetchone()
        except sqlite3.Error as exc:
            raise OrderLookupError("Order table unavailable") from exc
        return dict(zip(("id", "customer_id", "state"), row)) if row else None

    def ready(self) -> bool:
        try:
            db_uri = f"file:{quote(str(self.database), safe='/')}?mode=ro"
            with sqlite3.connect(db_uri, uri=True, timeout=2) as db:
                db.execute("SELECT id, customer_id, state FROM orders LIMIT 1").fetchone()
            return True
        except sqlite3.Error:
            return False


class DynamicStoreData(SqliteStoreData):
    """Read the current catalog and orders from SQLite on every request."""

    def __init__(self, order_database: Path, catalog_database: Path) -> None:
        self.database = order_database.resolve()
        self.catalog_database = catalog_database.resolve()
        if not self.database.is_file():
            raise FileNotFoundError(self.database)
        if not self.catalog_database.is_file():
            raise FileNotFoundError(self.catalog_database)

    @staticmethod
    def _terms(raw: str) -> list[str]:
        terms = json.loads(raw)
        if (not isinstance(terms, list) or len(terms) > 30 or
                any(not isinstance(item, str) or not item or len(item) > 100 for item in terms)):
            raise ValueError("Invalid catalog terms")
        return terms

    def catalog(self) -> tuple[list[dict], list[dict]]:
        db_uri = f"file:{quote(str(self.catalog_database), safe='/')}?mode=ro"
        try:
            with sqlite3.connect(db_uri, uri=True, timeout=2) as db:
                db.execute("BEGIN")
                policy_rows = db.execute(
                    "SELECT id, terms_json, answer FROM policies ORDER BY id").fetchall()
                product_rows = db.execute(
                    "SELECT id, name, terms_json, answer FROM products ORDER BY id").fetchall()
            policies = [{"id": row[0], "terms": self._terms(row[1]), "answer": row[2]}
                        for row in policy_rows]
            products = [{"id": row[0], "name": row[1], "terms": self._terms(row[2]),
                         "answer": row[3]} for row in product_rows]
            for record in policies + products:
                if (not isinstance(record["id"], str) or not record["id"] or
                        not isinstance(record["answer"], str) or not record["answer"] or
                        len(record["answer"]) > 4000):
                    raise ValueError("Invalid catalog record")
            if any(not isinstance(record["name"], str) or not record["name"] or
                   len(record["name"]) > 100
                   for record in products):
                raise ValueError("Invalid product name")
            return policies, products
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise CatalogError("Catalog unavailable or invalid") from exc

    def ready(self) -> bool:
        if not super().ready():
            return False
        try:
            self.catalog()
            return True
        except CatalogError:
            return False
