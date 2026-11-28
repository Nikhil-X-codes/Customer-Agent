"""Trusted local MCP adapter for the existing support decision flow."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .agent import SupportAgent
from .auth import verify_token
from .cli import make_agent
from .dotenv import load_dotenv
from .handoff import HandoffQueue


class MCPService:
    def __init__(self, agent: SupportAgent, queue: HandoffQueue, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("SESSION_SECRET must contain at least 32 characters")
        self.agent, self.queue, self.secret = agent, queue, secret

    def ask_support(self, question: str, session_token: str = "",
                    order_id: str = "") -> dict:
        """Answer a routine store question or create a human support ticket.

        Order status requires a signed storefront session token. Never pass a
        customer ID as an argument. Refunds, disputes, and uncertainty hand off.
        """
        if not isinstance(question, str) or len(question) > 2000:
            return {"error": "Invalid question"}
        if not isinstance(order_id, str) or len(order_id) > 64:
            return {"error": "Invalid order_id"}
        if not isinstance(session_token, str):
            return {"error": "Invalid session"}
        customer_id = verify_token(session_token, self.secret) if session_token else None
        if session_token and not customer_id:
            return {"error": "Invalid or expired session"}
        reply = self.agent.answer(question, customer_id, order_id or None)
        result = reply.to_dict()
        if reply.outcome == "handoff":
            try:
                result["ticket_id"] = self.queue.enqueue(reply, customer_id)
            except (sqlite3.Error, OSError):
                return {"error": "Support handoff unavailable. Please contact support directly."}
            result.pop("handoff")
        return result


def create_mcp_server(service: MCPService):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP support requires pip install 'customer-agent[mcp]'") from exc

    supply = getattr(service.agent, "domain", "support") == "supply_chain"
    instructions = ("Use ask_supply_chain for inventory, purchase orders, shipments, and "
                    "approved process questions. An authorized signed session_token is required. "
                    "Never claim a change was made; actions and exceptions go to a human."
                    if supply else
                    "Use ask_support for customer questions. Do not claim an order change or "
                    "human handoff unless the tool confirms it. Order questions require a "
                    "signed session_token from the trusted storefront.")
    server = FastMCP("supply-chain-agent" if supply else "customer-agent",
                     instructions=instructions)

    if supply:
        @server.tool()
        def ask_supply_chain(question: str, session_token: str = "") -> dict:
            """Check current supply records or create an operations review ticket."""
            return service.ask_support(question, session_token)
    else:
        @server.tool()
        def ask_support(question: str, session_token: str = "", order_id: str = "") -> dict:
            """Get a source-backed store answer, clarification, or human ticket."""
            return service.ask_support(question, session_token, order_id)

    return server


def main() -> None:
    env_path = Path(os.getenv("CUSTOMER_AGENT_ENV_FILE", ".env")).expanduser().resolve()
    load_dotenv(env_path)
    if env_path.is_file():
        os.chdir(env_path.parent)  # Resolve relative var/ paths beside the chosen .env.
    mode = os.getenv("APP_MODE")
    if mode != "production":
        raise SystemExit("Set APP_MODE=production explicitly for MCP")
    secret = os.getenv("SESSION_SECRET", "")
    if len(secret) < 32 or (mode == "production" and secret.startswith("replace-")):
        raise SystemExit("SESSION_SECRET must be a real random secret of at least 32 characters")
    if mode == "production" and not os.getenv("HANDOFF_DB_PATH"):
        raise SystemExit("Production requires HANDOFF_DB_PATH")
    if mode == "production" and not Path(os.getenv("HANDOFF_DB_PATH", "")).is_absolute():
        raise SystemExit("Production HANDOFF_DB_PATH must be absolute")
    agent = make_agent()
    queue = HandoffQueue(Path(os.getenv("HANDOFF_DB_PATH", "var/handoffs.db")))
    create_mcp_server(MCPService(agent, queue, secret)).run(transport="stdio")


if __name__ == "__main__":
    main()
