import unittest
from datetime import datetime, timedelta, timezone

from support_agent.agent import SupportAgent
from support_agent.auth import issue_token, verify_token
from support_agent.data import Status, StatusError


class FixedProvider:
    def __init__(self, status=None, error=False):
        self.status = status
        self.error = error

    def get(self, order_id):
        if self.error:
            raise StatusError("unavailable")
        return self.status


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = SupportAgent()

    def test_policy_is_answered_from_source(self):
        reply = self.agent.answer("What is your shipping policy?")
        self.assertEqual(reply.outcome, "answered")
        self.assertEqual(reply.sources, ["policy:shipping"])

    def test_wrong_customer_cannot_see_status(self):
        reply = self.agent.answer("Where is ORD-1002?", "cust-1")
        self.assertEqual(reply.reason, "order_not_found_for_customer")
        self.assertEqual(reply.sources, [])
        self.assertNotIn("delivered", reply.message)

    def test_conflicting_status_hands_off(self):
        reply = self.agent.answer("Where is ORD-1003?", "cust-1")
        self.assertEqual(reply.reason, "status_conflict")
        self.assertEqual(reply.handoff["order_id"], "ORD-1003")

    def test_stale_status_hands_off(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        agent = SupportAgent(status_provider=FixedProvider(Status(
            "ORD-1001", "in transit", "", old, "test")))
        self.assertEqual(agent.answer("Where is ORD-1001?", "cust-1").reason, "status_stale")

    def test_status_error_hands_off(self):
        agent = SupportAgent(status_provider=FixedProvider(error=True))
        self.assertEqual(agent.answer("Where is ORD-1001?", "cust-1").reason, "status_unavailable")

    def test_high_stakes_wins(self):
        reply = self.agent.answer("Refund my order ORD-1001", "cust-1")
        self.assertEqual(reply.reason, "high_stakes_request")

    def test_signed_token(self):
        secret = "a" * 32
        token = issue_token("cust-1", secret)
        self.assertEqual(verify_token(token, secret), "cust-1")
        self.assertIsNone(verify_token(token + "x", secret))
        self.assertIsNone(verify_token(issue_token("cust-1", secret, 1), "b" * 32))


if __name__ == "__main__":
    unittest.main()
