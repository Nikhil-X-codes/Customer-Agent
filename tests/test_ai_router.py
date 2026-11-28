import json
import unittest
from unittest.mock import patch

from test_helpers import test_agent
from support_agent.ai_router import GroqRouter, OpenAIRouter, Route, RouterError


class StubRouter:
    def __init__(self, route=None, fail=False):
        self.decision, self.fail = route, fail
        self.sources = None

    def route(self, question, sources):
        self.sources = sources
        if self.fail:
            raise RouterError("offline")
        return self.decision


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return FakeResponse(self.payload)


def model_response(action, source_id="", confidence=0.95):
    return json.dumps({
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text",
                    "text": json.dumps({"action": action, "source_id": source_id,
                                        "confidence": confidence})}]}],
    }).encode()


def groq_response(action, source_id="", confidence=0.95):
    return json.dumps({"choices": [{"message": {"content": json.dumps({
        "action": action, "source_id": source_id, "confidence": confidence})}}]}).encode()


class AIRouterTests(unittest.TestCase):
    def test_dynamic_paraphrase_uses_approved_source(self):
        router = StubRouter(Route("source", "policy:shipping", 0.94))
        reply = test_agent(router=router).answer("What would delivery cost me?")
        self.assertEqual(reply.reason, "ai_source_match")
        self.assertEqual(reply.sources, ["policy:shipping"])
        self.assertIn("$5", reply.message)

    def test_semantic_high_stakes_hands_off(self):
        router = StubRouter(Route("handoff", "", 0.96))
        reply = test_agent(router=router).answer("My card shows two identical transactions")
        self.assertEqual(reply.reason, "high_stakes_request")
        self.assertIsNotNone(router.sources)

    def test_hard_stop_overrides_model(self):
        router = StubRouter(Route("source", "policy:shipping", 0.99))
        reply = test_agent(router=router).answer("Refund my order")
        self.assertEqual(reply.reason, "high_stakes_request")
        self.assertIsNone(router.sources)

    def test_uncertain_or_unavailable_model_hands_off(self):
        self.assertEqual(test_agent(router=StubRouter(Route("source", "policy:shipping", 0.5)))
                         .answer("What would delivery cost me?").reason, "ai_uncertain")
        self.assertEqual(test_agent(router=StubRouter(fail=True))
                         .answer("What would delivery cost me?").reason, "ai_unavailable")

    def test_model_cannot_bypass_order_ownership(self):
        router = StubRouter(Route("order_status", "", 0.99))
        reply = test_agent(router=router).answer("Has my purchase arrived? ORD-1002", "cust-1")
        self.assertEqual(reply.reason, "order_not_found_for_customer")

    def test_model_cannot_invent_source(self):
        router = StubRouter(Route("source", "policy:imaginary", 0.99))
        reply = test_agent(router=router).answer("How does this work?")
        self.assertEqual(reply.reason, "ai_invalid_source")

    def test_model_cannot_answer_an_explicit_order_as_policy(self):
        router = StubRouter(Route("source", "policy:shipping", 0.99))
        reply = test_agent(router=router).answer("Shipping for order ORD-1001?", "cust-2")
        self.assertEqual(reply.reason, "order_not_found_for_customer")
        self.assertIsNone(router.sources)

    def test_openai_response_is_validated_and_identifiers_redacted(self):
        opener = FakeOpener(model_response("source", "policy:shipping"))
        router = OpenAIRouter("example-test-key")
        sources = [{"id": "policy:shipping", "title": "shipping", "answer": "Cost is $5."}]
        with patch("support_agent.ai_router.build_opener", return_value=opener):
            result = router.route("Shipping for ORD-1001, email me at a@example.com", sources)
        self.assertEqual(result.source_id, "policy:shipping")
        sent = json.loads(opener.request.data)
        self.assertIs(sent["store"], False)
        self.assertNotIn("ORD-1001", json.dumps(sent))
        self.assertNotIn("a@example.com", json.dumps(sent))

    def test_openai_rejects_forged_source(self):
        opener = FakeOpener(model_response("source", "policy:imaginary"))
        with patch("support_agent.ai_router.build_opener", return_value=opener):
            with self.assertRaises(RouterError):
                OpenAIRouter("example-test-key").route("Question", [
                    {"id": "policy:shipping", "title": "shipping", "answer": "Cost is $5."}])

    def test_groq_structured_route_and_redaction(self):
        opener = FakeOpener(groq_response("source", "policy:shipping"))
        sources = [{"id": "policy:shipping", "title": "shipping", "answer": "Cost is $5."}]
        with patch("support_agent.ai_router.build_opener", return_value=opener):
            route = GroqRouter("example-test-key").route(
                "What is shipping for ORD-1001? Email a@example.com", sources)
        self.assertEqual(route.source_id, "policy:shipping")
        self.assertEqual(opener.request.full_url, "https://api.groq.com/openai/v1/chat/completions")
        sent = json.loads(opener.request.data)
        self.assertEqual(sent["model"], "openai/gpt-oss-120b")
        self.assertIs(sent["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(sent["reasoning_effort"], "low")
        self.assertNotIn("ORD-1001", json.dumps(sent))
        self.assertNotIn("a@example.com", json.dumps(sent))

    def test_groq_rejects_forged_source(self):
        opener = FakeOpener(groq_response("source", "policy:imaginary"))
        with patch("support_agent.ai_router.build_opener", return_value=opener):
            with self.assertRaises(RouterError):
                GroqRouter("example-test-key").route("Question", [
                    {"id": "policy:shipping", "title": "shipping", "answer": "Cost is $5."}])

    def test_large_catalog_shortlists_relevant_product(self):
        class Catalog:
            policies = []
            products = ([{"id": f"item-{index}", "name": f"Generic Item {index}",
                          "terms": [], "answer": "A basic item."}
                         for index in range(60)] +
                        [{"id": "lantern", "name": "Obsidian Lantern", "terms": [],
                          "answer": "The Obsidian Lantern uses a rechargeable battery."}])

            def catalog(self):
                return self.policies, self.products

        router = StubRouter(Route("source", "product:lantern", 0.98))
        reply = test_agent(data=Catalog(), router=router).answer(
            "Does the Obsidian Lantern have a rechargeable battery?")
        self.assertEqual(reply.outcome, "answered")
        self.assertLessEqual(len(router.sources), 40)
        self.assertIn("product:lantern", [source["id"] for source in router.sources])


if __name__ == "__main__":
    unittest.main()
