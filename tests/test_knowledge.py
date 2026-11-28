from qdrant_client import QdrantClient
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from qdrant_client import QdrantClient

from test_helpers import test_agent
from support_agent.document_answer import (DocumentDecision, GroqDocumentAnswerer,
                                           validate_decision)
from support_agent.knowledge import KnowledgeBase, KnowledgeError, Passage
from vector_helper import FakeEmbedder


class StubAnswerer:
    def __init__(self, action="answer", quote=None):
        self.action, self.quote = action, quote

    def answer(self, question, passages):
        quote = self.quote or passages[0].text[:80]
        return DocumentDecision(self.action, "Standard shipping costs $7.",
                                [{"chunk_id": passages[0].chunk_id, "quote": quote}], 0.95)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.kb = KnowledgeBase(client=QdrantClient(":memory:"), embedder=FakeEmbedder())

    def test_upload_replace_search_and_delete(self):
        first = self.kb.add("Shipping Policy.md", b"Standard shipping costs $5 for small orders.")
        self.assertEqual(len(self.kb.search("shipping cost")), 1)
        self.assertEqual(len(self.kb.search("delivery price")), 1)
        second = self.kb.add("Shipping Policy.md", b"Standard shipping costs $7 for small orders.")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.kb.list_documents()), 1)
        self.assertIn("$7", self.kb.search("shipping cost")[0].text)
        self.assertNotIn("$5", self.kb.search("shipping cost")[0].text)
        self.assertTrue(self.kb.delete(second["id"]))
        self.assertEqual(self.kb.search("shipping cost"), [])

    def test_supply_and_support_collections_are_isolated(self):
        client = QdrantClient(":memory:")
        support = KnowledgeBase(client=client, embedder=FakeEmbedder())
        supply = KnowledgeBase(client=client, embedder=FakeEmbedder(),
                               collection="supply_chain_knowledge")
        support.add("shipping.txt", b"Standard shipping costs $7 for small orders.")
        self.assertEqual(supply.search("shipping cost"), [])
        supply.add("procurement.txt", b"Procurement reviews supplier delivery dates.")
        self.assertEqual([doc["name"] for doc in support.list_documents()], ["shipping.txt"])
        self.assertEqual([doc["name"] for doc in supply.list_documents()], ["procurement.txt"])

    def test_rejects_unsupported_or_empty_documents(self):
        with self.assertRaises(KnowledgeError):
            self.kb.add("script.py", b"print('hi')")
        with self.assertRaises(KnowledgeError):
            self.kb.add("empty.txt", b"   ")
        with self.assertRaises(KnowledgeError):
            self.kb.add("broken.pdf", b"not a PDF")

    def test_agent_answers_from_uploaded_evidence(self):
        self.kb.add("shipping.txt", b"Standard shipping costs $7 for small orders. Express costs $12.")
        agent = test_agent(knowledge=self.kb, document_answerer=StubAnswerer())
        reply = agent.answer("What is the shipping cost?")
        self.assertEqual(reply.reason, "document_answer")
        self.assertEqual(reply.message, reply.evidence[0]["quote"])
        self.assertEqual(reply.evidence[0]["title"], "shipping.txt")
        self.assertEqual(agent.answer("Refund my order").reason, "high_stakes_request")

    def test_invalid_evidence_hands_off(self):
        self.kb.add("shipping.txt", b"Standard shipping costs $7 for small orders.")
        agent = test_agent(knowledge=self.kb,
                             document_answerer=StubAnswerer(quote="Fabricated evidence text"))
        self.assertEqual(agent.answer("What is the shipping cost?").reason,
                         "document_answer_unavailable")

    def test_numeric_claim_must_be_in_quote(self):
        passage = Passage("chunk-1", "doc-1", "policy.txt",
                          "Standard shipping costs $7 for small orders.", -1.0)
        with self.assertRaises(ValueError):
            validate_decision({"action": "answer", "answer": "Shipping costs $99.",
                               "citations": [{"chunk_id": "chunk-1",
                                              "quote": "Standard shipping costs $7 for small orders."}],
                               "confidence": 0.95}, [passage])

    def test_document_excerpt_without_model_is_labeled(self):
        self.kb.add("shipping.txt", b"Standard shipping costs $7 for small orders.")
        reply = test_agent(knowledge=self.kb).answer("What is the shipping cost?")
        self.assertEqual(reply.reason, "document_excerpt")
        self.assertIn("shipping.txt", reply.message)

    def test_groq_selects_exact_quote_and_redacts_identifiers(self):
        passage = Passage("chunk-1", "doc-1", "shipping.txt",
                          "Standard shipping costs $7 for small orders.", -1.0)
        decision = {"action": "answer", "answer": "Standard shipping costs $7.",
                    "citations": [{"chunk_id": "chunk-1", "quote": passage.text}],
                    "confidence": 0.95}
        response = {"choices": [{"message": {"content": json.dumps(decision)}}]}
        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        class FakeOpener:
            def open(self, request, timeout):
                payload = json.loads(request.data)
                self_test.assertEqual(payload["model"], "openai/gpt-oss-120b")
                self_test.assertNotIn("ORD-1234", payload["messages"][1]["content"])
                self_test.assertNotIn("me@example.com", payload["messages"][1]["content"])
                return FakeResponse(json.dumps(response).encode())
        self_test = self
        with patch("support_agent.document_answer.build_opener", return_value=FakeOpener()):
            result = GroqDocumentAnswerer("test-key").answer(
                "What is shipping for ORD-1234? me@example.com", [passage])
        self.assertEqual(result.citations[0]["quote"], passage.text)


if __name__ == "__main__":
    unittest.main()
