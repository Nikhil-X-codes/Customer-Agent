"""Public SDK contract with isolated, changing source data."""

import tempfile
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from support_agent import AgentSDK, AuthenticatedActor
from support_agent.agent import Reply
from support_agent.data import Status
from support_agent.handoff import HandoffQueue
from support_agent.supply_llm import ProviderChain
from support_agent.supply import SupplyStore
from supply_fixture import seed_demo_supply


class Source:
    def __init__(self):
        self.available = True
        self.on_hand = 12
        self.reads = 0

    def ready(self):
        return self.available

    def inventory(self, sku):
        self.reads += 1
        return {"sku": sku, "product_name": "Test part", "location": "Warehouse",
                "on_hand": self.on_hand, "reserved": 2, "reorder_point": 5,
                "observed_at": datetime.now(timezone.utc).isoformat()}

    def inventory_all(self):
        return [self.inventory("SKU-ABC")]

    def purchase_order(self, identifier):
        return None

    def shipment(self, identifier):
        return None


class BrokenSink:
    def ready(self):
        return False

    def enqueue(self, reply: Reply, actor_id=None):
        raise OSError("ticket system offline")


class CustomerSource:
    def ready(self):
        return True

    def order_for_customer(self, order_id, customer_id):
        return {"id": order_id, "customer_id": customer_id, "state": "shipped"}

    def catalog(self):
        return ([{"id": "shipping", "terms": ["shipping"],
                  "answer": "Shipping details come from the company catalog."}], [])


class CustomerStatus:
    def get(self, order_id):
        return Status(order_id, "shipped", "", datetime.now(timezone.utc), "company status")


class RoutingProvider:
    name = "groq"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, tools=None, json_mode=False):
        self.calls += 1
        if json_mode:
            return {"content": json.dumps({"specialist": "inventory", "confidence": 0.95})}
        return {"tool_calls": [{"function": {"name": "get_inventory",
                                              "arguments": json.dumps({"sku": "SKU-ABC"})}}]}


class SDKTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sink = HandoffQueue(Path(self.temp.name) / "handoffs.db")
        self.source = Source()
        self.sdk = AgentSDK.for_supply_chain(self.source, self.sink)

    def test_unauthenticated_request_never_reads_supply_source(self):
        response = self.sdk.ask("Stock for SKU-ABC?")
        self.assertEqual(response.reason, "authentication_required")
        self.assertEqual(self.source.reads, 0)

    def test_current_source_is_read_each_time_and_explained(self):
        actor = AuthenticatedActor("employee-123")
        first = self.sdk.ask("Stock for SKU-ABC?", actor=actor)
        self.assertEqual(first.outcome, "answered")
        self.assertIn("10 available", first.message)
        self.assertEqual(first.explanation["source_ids"], ["inventory:SKU-ABC"])
        self.source.on_hand = 8
        second = self.sdk.ask("Stock for SKU-ABC?", actor=actor)
        self.assertIn("6 available", second.message)
        self.assertEqual(self.source.reads, 2)
        self.assertEqual(second.handoff_status, "not_required")
        self.assertNotIn("handoff", second.to_dict())

    def test_action_creates_ticket_with_verified_actor(self):
        result = self.sdk.ask("Adjust SKU-ABC", actor=AuthenticatedActor("employee-123"))
        self.assertEqual(result.reason, "action_requires_human")
        self.assertEqual(result.handoff_status, "queued")
        self.assertEqual(self.sink.list_open()[0]["customer_id"], "employee-123")
        self.assertEqual(result.ticket_id, self.sink.list_open()[0]["id"])

    def test_source_and_sink_fail_closed(self):
        self.source.available = False
        self.assertFalse(self.sdk.ready())
        result = self.sdk.ask("Stock for SKU-ABC?", actor=AuthenticatedActor("employee-123"))
        self.assertEqual(result.reason, "source_unavailable")
        self.assertEqual(result.handoff_status, "queued")
        self.assertEqual(self.source.reads, 0)
        broken = AgentSDK.for_supply_chain(self.source, BrokenSink())
        result = broken.ask("Stock for SKU-ABC?", actor=AuthenticatedActor("employee-123"))
        self.assertEqual(result.handoff_status, "unavailable")
        self.assertIsNone(result.ticket_id)
        self.assertIn("could not be recorded", result.message)

    def test_adapter_identifier_mismatch_hands_off(self):
        self.source.inventory = lambda sku: {**Source().inventory("SKU-OTHER"),
                                              "observed_at": datetime.now(timezone.utc).isoformat()}
        result = self.sdk.ask("Stock for SKU-ABC?", actor=AuthenticatedActor("employee-123"))
        self.assertEqual(result.reason, "inventory_invalid_or_stale")
        self.assertEqual(result.handoff_status, "queued")

    def test_adapter_exception_hands_off(self):
        def failed_read(sku):
            raise TimeoutError("upstream timeout")
        self.source.inventory = failed_read
        result = self.sdk.ask("Stock for SKU-ABC?", actor=AuthenticatedActor("employee-123"))
        self.assertEqual(result.reason, "source_unavailable")
        self.assertEqual(result.handoff_status, "queued")

    def test_company_support_adapters(self):
        sdk = AgentSDK.for_customer_support(CustomerSource(), CustomerStatus(), self.sink)
        result = sdk.ask("Where is ORD-1001?", actor=AuthenticatedActor("customer-123"))
        self.assertEqual(result.reason, "verified_status")
        self.assertEqual(result.outcome, "answered")

    def test_support_adapter_wrong_owner_and_naive_status_fail_closed(self):
        source = CustomerSource()
        source.order_for_customer = lambda order_id, customer_id: {
            "id": order_id, "customer_id": "someone-else", "state": "shipped"}
        sdk = AgentSDK.for_customer_support(source, CustomerStatus(), self.sink)
        result = sdk.ask("Where is ORD-1001?", actor=AuthenticatedActor("customer-123"))
        self.assertEqual(result.reason, "order_not_found_for_customer")
        self.assertEqual(result.sources, [])
        source.order_for_customer = lambda order_id, customer_id: {
            "id": order_id, "customer_id": customer_id, "state": "shipped"}
        status = CustomerStatus()
        status.get = lambda order_id: Status(order_id, "shipped", "", datetime.now(), "test")
        sdk = AgentSDK.for_customer_support(source, status, self.sink)
        result = sdk.ask("Where is ORD-1001?", actor=AuthenticatedActor("customer-123"))
        self.assertEqual(result.reason, "status_unavailable")
        self.assertEqual(result.handoff_status, "queued")

    def test_multi_agent_factory_uses_guarded_tool_result(self):
        provider = RoutingProvider()
        sdk = AgentSDK.for_supply_chain(self.source, self.sink,
                                        providers=ProviderChain(provider))
        result = sdk.ask("Stock for SKU-ABC?", actor=AuthenticatedActor("employee-123"))
        self.assertEqual(result.reason, "inventory_snapshot")
        self.assertEqual(provider.calls, 2)
        self.assertIn("tool:get_inventory", result.explanation["agent_steps"])

    def test_reject_untrusted_identity_shape(self):
        with self.assertRaises(TypeError):
            self.sdk.ask("Stock for SKU-ABC?", actor="employee-123")
        with self.assertRaises(ValueError):
            AuthenticatedActor(" ")

    def test_sdk_rejects_builtin_sqlite_source_without_live_checks(self):
        path = Path(self.temp.name) / "synthetic.db"
        seed_demo_supply(path)
        with self.assertRaisesRegex(ValueError, "live-source checks"):
            AgentSDK.for_supply_chain(SupplyStore(path), self.sink)


if __name__ == "__main__":
    unittest.main()
