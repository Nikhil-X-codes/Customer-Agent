"""Production source provenance and HTTPS gateway failures."""

from supply_fixture import seed_demo_supply

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from support_agent.cli import _collection_name, main, make_agent
from support_agent.supply import SupplyDataError, SupplyStore
from support_agent.supply_http import HttpSupplyStore


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = payloads
        self.paths = []
    def open(self, request, timeout):
        self.paths.append(request.full_url)
        assert request.get_header("Authorization") == "Bearer test-read-only-token"
        value = self.payloads.get(request.full_url)
        if value is None:
            raise HTTPError(request.full_url, 404, "missing", None, io.BytesIO())
        if isinstance(value, int):
            raise HTTPError(request.full_url, value, "error", None, io.BytesIO())
        return FakeResponse(json.dumps(value).encode())


class ProductionSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc).isoformat()

    def test_demo_database_cannot_be_used_as_production_source(self):
        demo = self.root / "demo-supply.db"
        seed_demo_supply(demo)
        with self.assertRaises(SupplyDataError):
            SupplyStore(demo, require_live=True)
        renamed = self.root / "renamed.db"
        demo.rename(renamed)
        with self.assertRaises(SupplyDataError):
            SupplyStore(renamed, require_live=True)

    def test_live_database_checks_sync_before_each_read(self):
        path = self.root / "erp.db"
        seed_demo_supply(path)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE source_metadata SET kind = 'live', source_system = 'Acme ERP', "
                       "synced_at = ?", (self.now,))
            db.commit()
        store = SupplyStore(path, require_live=True)
        self.assertTrue(store.ready())
        self.assertEqual(store.inventory("SKU-100")["on_hand"], 120)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with sqlite3.connect(path) as db:
            db.execute("UPDATE source_metadata SET synced_at = ?", (old,))
            db.commit()
        with self.assertRaises(SupplyDataError):
            store.inventory("SKU-100")
        self.assertFalse(store.ready())
        with sqlite3.connect(path) as db:
            db.execute("UPDATE source_metadata SET synced_at = ?", (self.now,))
            db.execute("INSERT INTO source_metadata VALUES ('live', 'Second ERP', ?)",
                       (self.now,))
            db.commit()
        self.assertFalse(store.ready())

    def test_make_agent_requires_exactly_one_real_source(self):
        settings = {"APP_MODE": "production", "AGENT_DOMAIN": "supply_chain",
                    "AI_MODE": "off"}
        with patch.dict("os.environ", settings, clear=True):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                make_agent()
        demo = self.root / "demo-supply.db"
        seed_demo_supply(demo)
        settings["SUPPLY_DB_PATH"] = str(demo)
        with patch.dict("os.environ", settings, clear=True):
            with self.assertRaises(SupplyDataError):
                make_agent()

    def test_demo_and_live_documents_use_separate_collections(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_collection_name("supply_chain", "production"),
                             "supply_chain_live_knowledge_v1")
        with patch.dict("os.environ", {"QDRANT_COLLECTION": "demo_docs"}, clear=True):
            with self.assertRaises(ValueError):
                _collection_name("supply_chain", "production")
        with patch.dict("os.environ", {"QDRANT_COLLECTION": "support_live_knowledge"}, clear=True):
            with self.assertRaises(ValueError):
                _collection_name("supply_chain", "production")

    def test_make_agent_can_use_real_https_gateway(self):
        settings = {"APP_MODE": "production", "AGENT_DOMAIN": "supply_chain",
                    "AI_MODE": "off", "SUPPLY_API_URL": "https://erp.example.test",
                    "SUPPLY_API_TOKEN": "test-read-only-token"}
        class LiveStore:
            def ready(self):
                return True
        store = LiveStore()
        with patch.dict("os.environ", settings, clear=True), \
             patch("support_agent.cli.HttpSupplyStore", return_value=store) as factory:
            agent = make_agent()
        self.assertIs(agent.data, store)
        self.assertEqual(factory.call_args.args[0], "https://erp.example.test")

    def test_https_api_reads_live_record_and_checks_identity(self):
        base = "https://erp.example.test"
        payloads = {
            base + "/health": {"status": "ready", "source": "Acme ERP", "synced_at": self.now},
            base + "/inventory/SKU-100": {"sku": "SKU-100", "product_name": "Steel Bottle",
                "location": "East DC", "on_hand": 120, "reserved": 25,
                "reorder_point": 40, "observed_at": self.now},
            base + "/inventory?limit=5001": {"items": [], "complete": True},
        }
        opener = FakeOpener(payloads)
        with patch("support_agent.supply_http.build_opener", return_value=opener):
            store = HttpSupplyStore(base, "test-read-only-token")
            self.assertTrue(store.ready())
            self.assertEqual(store.inventory("SKU-100")["reserved"], 25)
            self.assertEqual(store.inventory_all(), [])
            self.assertIsNone(store.shipment("SHP-999"))
            payloads[base + "/inventory/SKU-100"]["sku"] = "SKU-OTHER"
            with self.assertRaises(SupplyDataError):
                store.inventory("SKU-100")

    def test_https_api_rejects_stale_demo_and_incomplete_data(self):
        base = "https://erp.example.test"
        payloads = {base + "/health": {"status": "ready", "source": "demo feed",
                                          "synced_at": self.now},
                    base + "/inventory?limit=5001": {"items": [], "complete": False}}
        with patch("support_agent.supply_http.build_opener",
                   return_value=FakeOpener(payloads)):
            store = HttpSupplyStore(base, "test-read-only-token")
            self.assertFalse(store.ready())
            with self.assertRaises(SupplyDataError):
                store.inventory_all()
            payloads[base + "/health"]["source"] = "Acme ERP"
            with self.assertRaises(SupplyDataError):
                store.inventory_all()
            payloads[base + "/health"]["synced_at"] = (
                datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            self.assertFalse(store.ready())
        with self.assertRaises(ValueError):
            HttpSupplyStore("http://erp.example.test", "test-read-only-token")

    def test_serve_requires_explicit_mode_and_loopback_demo(self):
        with patch("support_agent.cli.load_dotenv"), patch.dict("os.environ", {}, clear=True), \
             patch.object(sys, "argv", ["customer-agent", "serve"]):
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
        with patch("support_agent.cli.load_dotenv"), patch.dict("os.environ", {
            "APP_MODE": "demo", "SESSION_SECRET": "s" * 32}, clear=True), \
             patch.object(sys, "argv", ["customer-agent", "serve", "--host", "0.0.0.0"]):
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)

    def test_production_disables_demo_commands(self):
        commands = (["ask", "Stock for SKU-100?", "--customer-id", "planner-1"],
                    ["token", "planner-1"], ["demo"], ["eval"])
        for command in commands:
            with self.subTest(command=command), patch("support_agent.cli.load_dotenv"), \
                 patch.dict("os.environ", {"APP_MODE": "production"}, clear=True), \
                 patch.object(sys, "argv", ["customer-agent", *command]):
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
