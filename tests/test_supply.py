"""Supply mode behavior across read-only records, HTTP, and MCP."""

from supply_fixture import seed_demo_supply

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from qdrant_client import QdrantClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from support_agent.auth import issue_token
from support_agent.cli import build_handler
from support_agent.handoff import HandoffQueue
from support_agent.mcp_server import MCPService, create_mcp_server
from support_agent.knowledge import KnowledgeBase
from support_agent.document_answer import DocumentDecision
from support_agent.supply import SupplyChainAgent, SupplyStore
from vector_helper import FakeEmbedder


class SupplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "supply.db"
        seed_demo_supply(self.path)
        self.agent = SupplyChainAgent(SupplyStore(self.path))
        self.queue = HandoffQueue(Path(self.temp.name) / "handoffs.db")
        self.secret = "s" * 32

    def test_dynamic_inventory_and_invalid_values(self):
        before = self.agent.answer("Stock for SKU-100?", "planner-1")
        self.assertIn("95 available", before.message)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE inventory SET on_hand = 70 WHERE sku = 'SKU-100'")
            db.commit()
        after = self.agent.answer("Stock for SKU-100?", "planner-1")
        self.assertIn("45 available", after.message)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE inventory SET reserved = 90 WHERE sku = 'SKU-100'")
            db.commit()
        self.assertEqual(self.agent.answer("Stock for SKU-100?", "planner-1").reason,
                         "inventory_invalid_or_stale")

    def test_stale_shipment_and_unknown_state_handoff(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE shipments SET observed_at = ? WHERE id = 'SHP-3001'", (old,))
            db.commit()
        self.assertEqual(self.agent.answer("Where is SHP-3001?", "planner-1").reason,
                         "shipment_invalid_or_stale")

    def test_low_stock_requires_complete_fresh_snapshot(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE inventory SET observed_at = ? WHERE sku = 'SKU-100'", (old,))
            db.commit()
        self.assertEqual(self.agent.answer("Which SKUs are low stock?", "planner-1").reason,
                         "inventory_invalid_or_stale")

    def test_overdue_purchase_order_hands_off(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE purchase_orders SET expected_at = ? WHERE id = 'PO-2001'",
                       (yesterday,))
            db.commit()
        self.assertEqual(self.agent.answer("Status of PO-2001?", "planner-1").reason,
                         "purchase_order_overdue")

    def test_explicit_reference_wins_over_generic_inventory_word(self):
        self.assertEqual(self.agent.answer("What quantity is in PO-2001?", "planner-1").reason,
                         "purchase_order_snapshot")
        self.assertEqual(self.agent.answer("Which shipment carries PO-2001?", "planner-1").reason,
                         "shipment_id_required")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE shipments SET observed_at = ?, state = 'lost' "
                       "WHERE id = 'SHP-3001'", (datetime.now(timezone.utc).isoformat(),))
            db.commit()
        self.assertEqual(self.agent.answer("Where is SHP-3001?", "planner-1").reason,
                         "shipment_invalid_or_stale")

    def test_http_requires_session_and_queues_overdue(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(
            self.agent, self.secret, self.queue))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/ui/config", timeout=3) as response:
            self.assertEqual(json.load(response)["domain"], "supply_chain")
        with self.assertRaises(Exception):
            urlopen(base + "/demo/session", timeout=3)
        def ask(question, token=""):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            request = Request(base + "/chat", data=json.dumps({"question": question}).encode(),
                              headers=headers)
            with urlopen(request, timeout=3) as response:
                return json.load(response)
        self.assertEqual(ask("Stock at SKU-100?")["reason"], "authentication_required")
        token = issue_token("planner-1", self.secret)
        self.assertEqual(ask("Stock at SKU-100?", token)["reason"], "inventory_snapshot")
        handoff = ask("Where is SHP-3003?", token)
        self.assertEqual(handoff["reason"], "shipment_overdue")
        self.assertIn("ticket_id", handoff)
        self.assertEqual(len(self.queue.list_open()), 1)

    def test_mcp_exposes_supply_tool_only(self):
        service = MCPService(self.agent, self.queue, self.secret)
        server = create_mcp_server(service)
        async def check():
            tools = await server.list_tools()
            self.assertEqual([tool.name for tool in tools], ["ask_supply_chain"])
        asyncio.run(check())
        token = issue_token("planner-1", self.secret)
        self.assertEqual(service.ask_support("Stock at SKU-100?", token)["reason"],
                         "inventory_snapshot")

    def test_mcp_stdio_supply_answer(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE source_metadata SET kind = ?, source_system = ?, synced_at = ?",
                       ("live", "test-integration", datetime.now(timezone.utc).isoformat()))
            db.commit()
        async def check():
            env = dict(os.environ)
            env.update({"AGENT_DOMAIN": "supply_chain", "APP_MODE": "production", "AI_MODE": "off",
                        "SUPPLY_DB_PATH": str(self.path), "SESSION_SECRET": self.secret,
                        "HANDOFF_DB_PATH": str(Path(self.temp.name) / "mcp-handoffs.db"),
                        "CUSTOMER_AGENT_ENV_FILE": str(Path(self.temp.name) / "absent.env")})
            params = StdioServerParameters(command=sys.executable,
                                           args=["-m", "support_agent.mcp_server"], env=env)
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual([tool.name for tool in tools.tools], ["ask_supply_chain"])
                    token = issue_token("planner-1", self.secret)
                    result = await session.call_tool("ask_supply_chain", {
                        "question": "Stock at SKU-100?", "session_token": token})
                    self.assertFalse(result.isError)
                    data = result.structuredContent or json.loads(result.content[0].text)
                    self.assertEqual(data["reason"], "inventory_snapshot")
        asyncio.run(check())

    def test_guidance_uses_separate_evidence_checked_collection(self):
        knowledge = KnowledgeBase(client=QdrantClient(":memory:"), embedder=FakeEmbedder(),
                                  collection="supply_chain_knowledge")
        knowledge.add("stock-policy.txt", b"Stock adjustments require operations approval.")
        class Answerer:
            def answer(self, question, passages):
                return DocumentDecision("answer", "Stock adjustments require operations approval.",
                                        [{"chunk_id": passages[0].chunk_id,
                                          "quote": passages[0].text}], 0.95)
        agent = SupplyChainAgent(SupplyStore(self.path), knowledge=knowledge,
                                 document_answerer=Answerer())
        reply = agent.answer("What is the stock adjustment policy?", "planner-1")
        self.assertEqual(reply.reason, "document_answer")
        self.assertEqual(reply.message, "Stock adjustments require operations approval.")


if __name__ == "__main__":
    unittest.main()

class ExplanationTests(unittest.TestCase):
    def test_verified_record_and_handoff_explanations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.db'
            seed_demo_supply(path)
            agent = SupplyChainAgent(SupplyStore(path))
            answer = agent.answer('Stock for SKU-100?', 'planner-1').to_dict()
            self.assertEqual(answer['explanation']['reason_code'], 'inventory_snapshot')
            self.assertIn('Available = on hand − reserved', answer['explanation']['checks'])
            self.assertEqual(answer['explanation']['source_ids'], ['inventory:SKU-100'])
            handoff = agent.answer('Adjust SKU-100', 'planner-1').to_dict()
            self.assertEqual(handoff['explanation']['decision'], 'handoff')
            self.assertEqual(handoff['explanation']['checks'], [])
