"""Read-only supply chain records and conservative operational answers."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .agent import Reply, SupportAgent
from .knowledge import KnowledgeBase
from .document_answer import DocumentAnswerer


class SupplyDataError(Exception):
    pass


class SupplyStore:
    def __init__(self, path: Path, *, require_live: bool = False,
                 max_sync_age: timedelta = timedelta(minutes=30)) -> None:
        self.path = path.resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        if not timedelta(0) < max_sync_age <= timedelta(days=1):
            raise ValueError("Supply sync age limit must be 1 minute to 24 hours")
        self.require_live, self.max_sync_age = require_live, max_sync_age
        if require_live:
            if "demo-supply" in self.path.name.lower():
                raise SupplyDataError("Demo supply database is not a production source")
            self._assert_live_source()

    def _assert_live_source(self) -> None:
        uri = f"file:{quote(str(self.path), safe='/')}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=2) as db:
                rows = db.execute("SELECT kind, source_system, synced_at "
                                  "FROM source_metadata LIMIT 2").fetchall()
        except sqlite3.Error as exc:
            raise SupplyDataError("Live supply source metadata is unavailable") from exc
        row = rows[0] if len(rows) == 1 else None
        if (not row or row[0] != "live" or not isinstance(row[1], str) or
                len(row[1].strip()) < 3 or
                any(term in row[1].lower() for term in ("demo", "mock", "fixture")) or
                not _fresh(row[2], self.max_sync_age)):
            raise SupplyDataError("Live supply source is missing or stale")

    def _read(self, sql: str, args: tuple = ()) -> list[dict]:
        if self.require_live:
            self._assert_live_source()
        uri = f"file:{quote(str(self.path), safe='/')}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=2) as db:
                db.row_factory = sqlite3.Row
                return [dict(row) for row in db.execute(sql, args)]
        except sqlite3.Error as exc:
            raise SupplyDataError("Supply data unavailable") from exc

    def inventory(self, sku: str) -> dict | None:
        rows = self._read("SELECT sku, product_name, location, on_hand, reserved, "
                          "reorder_point, observed_at FROM inventory WHERE sku = ?", (sku,))
        return rows[0] if rows else None

    def inventory_all(self) -> list[dict]:
        return self._read("SELECT sku, product_name, location, on_hand, reserved, "
                          "reorder_point, observed_at FROM inventory "
                          "ORDER BY sku LIMIT 5001")

    def purchase_order(self, identifier: str) -> dict | None:
        rows = self._read("SELECT id, supplier, sku, quantity, state, expected_at, observed_at "
                          "FROM purchase_orders WHERE id = ?", (identifier,))
        return rows[0] if rows else None

    def shipment(self, identifier: str) -> dict | None:
        rows = self._read("SELECT id, purchase_order_id, carrier, state, eta, observed_at "
                          "FROM shipments WHERE id = ?", (identifier,))
        return rows[0] if rows else None

    def ready(self) -> bool:
        try:
            if self.require_live:
                self._assert_live_source()
            for table in ("inventory", "purchase_orders", "shipments"):
                self._read(f"SELECT 1 FROM {table} LIMIT 1")
            return True
        except SupplyDataError:
            return False


ACTION = re.compile(r"\b(?:place|create|approve|cancel|expedite|reroute|change|update|adjust|"
                    r"write.?off|pay|book|reorder(?! point)|order more|buy|purchase(?! order)|"
                    r"transfer|schedule|promise|commit|negotiate|dispute|chargeback|"
                    r"release|allocate)\b", re.I)
SKU = re.compile(r"\bSKU-[A-Z0-9-]{2,32}\b", re.I)
PO = re.compile(r"\bPO-[A-Z0-9-]{2,32}\b", re.I)
SHIPMENT = re.compile(r"\bSHP-[A-Z0-9-]{2,32}\b", re.I)
INVENTORY_WORDS = re.compile(r"\b(?:inventory|stock|available|on.hand|reserved|units|quantity|balance|sku)\b", re.I)
PO_WORDS = re.compile(r"\b(?:purchase order|supplier order|procurement order|\bpo\b)\b", re.I)
SHIP_WORDS = re.compile(r"\b(?:shipment|freight|carrier|delivery|\beta\b)\b", re.I)
LOW_STOCK = re.compile(r"\b(?:low stock|low inventory|below reorder point|under reorder point|"
                       r"stock risk|shortages?|running low)\b", re.I)
PO_STATES = {"draft", "submitted", "confirmed", "in transit", "partially received", "received"}
SHIP_STATES = {"planned", "picked up", "in transit", "out for delivery", "delivered"}


def _fresh(value: str, max_age: timedelta) -> bool:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return False
        age = datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
        return timedelta(0) <= age <= max_age
    except (TypeError, ValueError):
        return False


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None and "T" in value:
            return None
        return date.date().isoformat()
    except (TypeError, ValueError):
        return None


class SupplyChainAgent(SupportAgent):
    domain = "supply_chain"

    def __init__(self, store: SupplyStore, *, knowledge: KnowledgeBase | None = None,
                 document_answerer: DocumentAnswerer | None = None) -> None:
        super().__init__(data=store, knowledge=knowledge,
                         document_answerer=document_answerer)

    def answer(self, question: str, customer_id: str | None = None,
               order_id: str | None = None) -> Reply:
        question = question.strip()
        if not question:
            return Reply("clarify", "What supply chain detail can I check?", "empty_question", [])
        if len(question) > 2000:
            return self._handoff("I’ll ask the operations team to review this request.",
                                 "question_too_long", question)
        if not customer_id:
            return Reply("clarify", "Sign in with an authorized operations account first.",
                         "authentication_required", [])
        if ACTION.search(question):
            return self._handoff("I’ll send this action request to the operations team.",
                                 "action_requires_human", question)
        sku = SKU.search(question)
        po = PO.search(question)
        shipment = SHIPMENT.search(question)
        matches = sum(bool(item) for item in (sku, po, shipment))
        if matches > 1:
            return Reply("clarify", "Which one record should I check first?",
                         "multiple_references", [])
        if po and SHIP_WORDS.search(question):
            return Reply("clarify", "Which shipment ID should I check for that purchase order?",
                         "shipment_id_required", [])
        if not matches and re.search(r"\b(?:policy|procedure|guideline|process|SOP)\b", question, re.I):
            document_reply = self._answer_from_knowledge(question)
            if document_reply:
                return document_reply
            return self._handoff("I can’t verify that process from approved guidance. "
                                 "I’ll ask the team.", "no_verified_answer", question)
        try:
            if sku:
                return self._inventory(question, sku.group(0).upper())
            if po:
                return self._purchase_order(question, po.group(0).upper())
            if shipment:
                return self._shipment(question, shipment.group(0).upper())
            if LOW_STOCK.search(question) or INVENTORY_WORDS.search(question):
                return self._inventory(question, None)
            if PO_WORDS.search(question):
                return self._purchase_order(question, None)
            if SHIP_WORDS.search(question):
                return self._shipment(question, None)
        except SupplyDataError:
            return self._handoff("I can’t verify current operations data. I’ll ask the team.",
                                 "supply_data_unavailable", question)
        document_reply = self._answer_from_knowledge(question)
        if document_reply:
            return document_reply
        return self._handoff("I can’t verify that from current records. I’ll ask the team.",
                             "no_verified_answer", question)

    def _inventory(self, question: str, sku: str | None) -> Reply:
        if not sku:
            if not LOW_STOCK.search(question):
                return Reply("clarify", "Which SKU should I check?", "sku_required", [])
            all_rows = self.data.inventory_all()
            if not all_rows:
                return self._handoff("I don’t have a current inventory snapshot. I’ll ask the team.",
                                     "inventory_empty", question)
            if len(all_rows) > 5000 or any(not self._valid_inventory(row) for row in all_rows):
                return self._handoff("Some stock records need review. I’ll ask the team.",
                                     "inventory_invalid_or_stale", question)
            rows = [row for row in all_rows
                    if row["on_hand"] - row["reserved"] <= row["reorder_point"]]
            if len(rows) > 30:
                return self._handoff("There are too many low-stock records to summarize safely. "
                                     "I’ll ask the team.", "low_stock_review", question)
            if not rows:
                return Reply("answered", "No SKUs are at or below their reorder points in current records.",
                             "low_stock_check", ["inventory:low-stock"])
            names = [f"{row['sku']} ({row['on_hand'] - row['reserved']} available at {row['location']})"
                     for row in rows]
            return Reply("answered", "At or below reorder point: " + "; ".join(names) + ".",
                         "low_stock_check", [f"inventory:{row['sku']}" for row in rows])
        row = self.data.inventory(sku)
        if not row:
            return Reply("clarify", "I couldn’t find that SKU. Check the identifier.",
                         "sku_not_found", [])
        if row.get("sku") != sku:
            return self._handoff("This stock record needs review. I’ll ask the team.",
                                 "inventory_invalid_or_stale", question)
        if not self._valid_inventory(row):
            return self._handoff("This stock record needs review. I’ll ask the team.",
                                 "inventory_invalid_or_stale", question, [f"inventory:{sku}"])
        available = row["on_hand"] - row["reserved"]
        risk = " At or below its reorder point." if available <= row["reorder_point"] else ""
        return Reply("answered", f"{sku} ({row['product_name']}) at {row['location']}: "
                     f"{row['on_hand']} on hand, {row['reserved']} reserved, "
                     f"{available} available.{risk}", "inventory_snapshot", [f"inventory:{sku}"])

    @staticmethod
    def _valid_inventory(row: dict) -> bool:
        values = (row["on_hand"], row["reserved"], row["reorder_point"])
        return (_fresh(row["observed_at"], timedelta(hours=24)) and
                all(type(value) is int and value >= 0 for value in values) and
                row["reserved"] <= row["on_hand"])

    def _purchase_order(self, question: str, identifier: str | None) -> Reply:
        if not identifier:
            return Reply("clarify", "Which purchase order ID should I check?", "po_id_required", [])
        row = self.data.purchase_order(identifier)
        if not row:
            return Reply("clarify", "I couldn’t find that purchase order.", "po_not_found", [])
        if row.get("id") != identifier:
            return self._handoff("This purchase order needs review. I’ll ask the team.",
                                 "po_invalid_or_stale", question)
        if (not _fresh(row["observed_at"], timedelta(hours=24)) or
                row["state"] not in PO_STATES or type(row["quantity"]) is not int or
                row["quantity"] < 0):
            return self._handoff("This purchase order needs review. I’ll ask the team.",
                                 "po_invalid_or_stale", question, [f"purchase_orders:{identifier}"])
        expected = _date(row["expected_at"])
        if (expected and expected < datetime.now(timezone.utc).date().isoformat() and
                row["state"] != "received"):
            return self._handoff("The purchase order’s expected date has passed. "
                                 "I’ll ask procurement to review it.",
                                 "purchase_order_overdue", question,
                                 [f"purchase_orders:{identifier}"])
        suffix = f" Recorded expected date: {expected}." if expected else ""
        return Reply("answered", f"{identifier} for {row['quantity']} units of {row['sku']} "
                     f"from {row['supplier']} is {row['state']}.{suffix}",
                     "purchase_order_snapshot", [f"purchase_orders:{identifier}"])

    def _shipment(self, question: str, identifier: str | None) -> Reply:
        if not identifier:
            return Reply("clarify", "Which shipment ID should I check?", "shipment_id_required", [])
        row = self.data.shipment(identifier)
        if not row:
            return Reply("clarify", "I couldn’t find that shipment.", "shipment_not_found", [])
        if row.get("id") != identifier:
            return self._handoff("This shipment status needs review. I’ll ask the team.",
                                 "shipment_invalid_or_stale", question)
        source = [f"shipments:{identifier}"]
        if not _fresh(row["observed_at"], timedelta(hours=2)) or row["state"] not in SHIP_STATES:
            return self._handoff("This shipment status needs review. I’ll ask the team.",
                                 "shipment_invalid_or_stale", question, source)
        eta = _date(row["eta"])
        if eta and eta < datetime.now(timezone.utc).date().isoformat() and row["state"] != "delivered":
            return self._handoff("The recorded ETA has passed without a delivery update. "
                                 "I’ll ask the logistics team to check.",
                                 "shipment_overdue", question, source)
        suffix = f" Recorded ETA: {eta} (estimate)." if eta else ""
        return Reply("answered", f"{identifier} for {row['purchase_order_id']} is "
                     f"{row['state']} with {row['carrier']}.{suffix}",
                     "shipment_snapshot", source)
