"""MCP boundary: signed identity, support tickets, and stdio tool discovery."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_helpers import test_agent
from support_agent.auth import issue_token
from support_agent.handoff import HandoffQueue
from support_agent.mcp_server import MCPService


class MCPServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = HandoffQueue(Path(self.temp.name) / "handoffs.db")
        self.secret = "m" * 32
        self.service = MCPService(test_agent(), self.queue, self.secret)

    def test_signed_order_and_handoff(self):
        token = issue_token("cust-1", self.secret)
        answer = self.service.ask_support("Where is order ORD-1001?", token)
        self.assertEqual(answer["reason"], "verified_status")
        self.assertEqual(self.service.ask_support("Where is order ORD-1001?")["reason"],
                         "authentication_required")
        self.assertEqual(self.service.ask_support("Where is order ORD-1001?", "bad")["error"],
                         "Invalid or expired session")
        handoff = self.service.ask_support("Refund my order ORD-1001", token)
        self.assertEqual(handoff["reason"], "high_stakes_request")
        self.assertIn("ticket_id", handoff)
        self.assertEqual(len(self.queue.list_open()), 1)
        self.assertEqual(self.queue.list_open()[0]["customer_id"], "cust-1")
        self.assertNotIn("handoff", handoff)



if __name__ == "__main__":
    unittest.main()
