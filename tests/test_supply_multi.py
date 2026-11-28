"""Multi-agent routing, function calls, and provider failover without real keys."""

from supply_fixture import seed_demo_supply

import json
import io
import threading
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from qdrant_client import QdrantClient

from support_agent.knowledge import KnowledgeBase
from support_agent.auth import issue_token
from support_agent.cli import build_handler
from support_agent.handoff import HandoffQueue
from support_agent.supply import SupplyChainAgent, SupplyStore
from support_agent.supply_llm import ChatProvider, ProviderChain, ProviderError
from support_agent.supply_multi import SupplyMultiAgent
from vector_helper import FakeEmbedder


def route(specialist, confidence=0.95):
    return {"content": json.dumps({"specialist": specialist, "confidence": confidence})}


def tool(name, args):
    return {"tool_calls": [{"function": {"name": name,
                                          "arguments": json.dumps(args)}}]}


class FakeProvider:
    def __init__(self, name, responses):
        self.name, self.responses, self.calls = name, list(responses), []

    def complete(self, messages, *, tools=None, json_mode=False):
        self.calls.append({"messages": messages, "tools": tools, "json_mode": json_mode})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MultiAgentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "supply.db"
        seed_demo_supply(path)
        self.base = SupplyChainAgent(SupplyStore(path))

    def test_supervisor_and_inventory_specialist_call_tool(self):
        primary = FakeProvider("groq", [route("inventory"),
                                        tool("get_inventory", {"sku": "SKU-100"})])
        agent = SupplyMultiAgent(self.base, ProviderChain(primary))
        reply = agent.answer("How many units are available for SKU-100?", "planner-1")
        self.assertEqual(reply.reason, "inventory_snapshot")
        self.assertIn("95 available", reply.message)
        self.assertEqual(reply.sources, ["inventory:SKU-100"])
        self.assertEqual(reply.agent_trace, ["supervisor:groq", "specialist:inventory:groq",
                                             "tool:get_inventory", "risk:passed"])
        self.assertEqual(primary.calls[1]["tools"][0]["function"]["name"], "get_inventory")

    def test_fallback_on_retryable_groq_failure(self):
        primary = FakeProvider("groq", [ProviderError("rate limit", retryable=True),
                                        ProviderError("timeout", retryable=True)])
        fallback = FakeProvider("openrouter", [route("procurement"),
                                               tool("get_purchase_order",
                                                    {"purchase_order_id": "PO-2001"})])
        reply = SupplyMultiAgent(self.base, ProviderChain(primary, fallback)).answer(
            "Status of PO-2001?", "planner-1")
        self.assertEqual(reply.reason, "purchase_order_snapshot")
        self.assertIn("supervisor:openrouter", reply.agent_trace)
        self.assertIn("specialist:procurement:openrouter", reply.agent_trace)

    def test_invalid_reference_hands_off_without_fallback(self):
        primary = FakeProvider("groq", [route("logistics"),
                                        tool("get_shipment", {"shipment_id": "SHP-9999"})])
        fallback = FakeProvider("openrouter", [])
        reply = SupplyMultiAgent(self.base, ProviderChain(primary, fallback)).answer(
            "Where is SHP-3001?", "planner-1")
        self.assertEqual(reply.reason, "agent_reference_invalid")
        self.assertEqual(fallback.calls, [])

    def test_low_confidence_and_nonretryable_error_stop(self):
        primary = FakeProvider("groq", [route("inventory", 0.4)])
        reply = SupplyMultiAgent(self.base, ProviderChain(primary)).answer(
            "Stock for SKU-100?", "planner-1")
        self.assertEqual(reply.reason, "agent_route_uncertain")
        self.assertEqual(len(primary.calls), 1)
        primary = FakeProvider("groq", [ProviderError("bad key", retryable=False)])
        fallback = FakeProvider("openrouter", [route("inventory")])
        reply = SupplyMultiAgent(self.base, ProviderChain(primary, fallback)).answer(
            "Stock for SKU-100?", "planner-1")
        self.assertEqual(reply.reason, "agent_provider_unavailable")
        self.assertEqual(fallback.calls, [])

    def test_malformed_tool_and_overdue_record_stop_safely(self):
        primary = FakeProvider("groq", [route("logistics"), {"tool_calls": [None]}])
        reply = SupplyMultiAgent(self.base, ProviderChain(primary)).answer(
            "Where is SHP-3001?", "planner-1")
        self.assertEqual(reply.reason, "agent_tool_invalid")
        primary = FakeProvider("groq", [route("logistics"),
                                        tool("get_shipment", {"shipment_id": "SHP-3003"})])
        reply = SupplyMultiAgent(self.base, ProviderChain(primary)).answer(
            "Where is SHP-3003?", "planner-1")
        self.assertEqual(reply.reason, "shipment_overdue")
        self.assertEqual(reply.agent_trace[-1], "risk:handoff")

    def test_auth_and_actions_bypass_models(self):
        primary = FakeProvider("groq", [])
        agent = SupplyMultiAgent(self.base, ProviderChain(primary))
        self.assertEqual(agent.answer("Stock for SKU-100?").reason, "authentication_required")
        self.assertEqual(agent.answer("Reorder SKU-100", "planner-1").reason,
                         "action_requires_human")
        self.assertEqual(agent.answer("Order more SKU-100", "planner-1").reason,
                         "action_requires_human")
        self.assertEqual(primary.calls, [])

    def test_guidance_quotes_approved_document(self):
        kb = KnowledgeBase(client=QdrantClient(":memory:"), embedder=FakeEmbedder(),
                           collection="supply_chain_knowledge")
        kb.add("stock-policy.txt", b"Stock adjustments require operations approval.")
        base = SupplyChainAgent(self.base.data, knowledge=kb)
        passage = kb.search("stock adjustment policy")[0]
        response = {"content": json.dumps({"action": "answer",
            "answer": "Stock adjustments require operations approval.",
            "citations": [{"chunk_id": passage.chunk_id, "quote": passage.text}],
            "confidence": 0.95})}
        primary = FakeProvider("groq", [route("guidance"), response])
        reply = SupplyMultiAgent(base, ProviderChain(primary)).answer(
            "What is the stock adjustment policy?", "planner-1")
        self.assertEqual(reply.reason, "document_answer")
        self.assertEqual(reply.message, passage.text)
        self.assertIn("risk:passed", reply.agent_trace)

    def test_provider_http_errors_classify_failover(self):
        provider = ChatProvider("groq", "https://api.groq.com/openai/v1/chat/completions",
                                "test-key", "openai/gpt-oss-120b")
        class Opener:
            def __init__(self, code):
                self.code = code
            def open(self, request, timeout):
                raise HTTPError(request.full_url, self.code, "failure", None, io.BytesIO())
        with patch("support_agent.supply_llm.build_opener", return_value=Opener(429)):
            with self.assertRaises(ProviderError) as error:
                provider.complete([{"role": "user", "content": "test"}], json_mode=True)
            self.assertTrue(error.exception.retryable)
        with patch("support_agent.supply_llm.build_opener", return_value=Opener(401)):
            with self.assertRaises(ProviderError) as error:
                provider.complete([{"role": "user", "content": "test"}], json_mode=True)
            self.assertFalse(error.exception.retryable)

    def test_http_multi_agent_invalid_tool_creates_ticket(self):
        primary = FakeProvider("groq", [route("inventory"),
                                        tool("get_inventory", {"sku": "SKU-999"})])
        agent = SupplyMultiAgent(self.base, ProviderChain(primary))
        queue = HandoffQueue(Path(self.base.data.path).parent / "multi-handoffs.db")
        secret = "m" * 32
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(
            agent, secret, queue))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = Request(f"http://127.0.0.1:{server.server_port}/chat",
                          data=json.dumps({"question": "Stock for SKU-100?"}).encode(),
                          headers={"Content-Type": "application/json",
                                   "Authorization": "Bearer " + issue_token("planner-1", secret)})
        with urlopen(request, timeout=3) as response:
            data = json.load(response)
        self.assertEqual(data["reason"], "agent_reference_invalid")
        self.assertIn("ticket_id", data)
        self.assertEqual(len(queue.list_open()), 1)


if __name__ == "__main__":
    unittest.main()
