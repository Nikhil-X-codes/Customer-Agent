"""CLI and minimal HTTP entry points."""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agent import SupportAgent
from .auth import issue_token, verify_token
from .data import HttpStatusProvider


def make_agent() -> SupportAgent:
    base_url = os.getenv("STATUS_API_URL")
    if base_url:
        return SupportAgent(status_provider=HttpStatusProvider(
            base_url, os.getenv("STATUS_API_TOKEN", "")))
    return SupportAgent()


def serve(agent: SupportAgent, host: str, port: int, secret: str) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/chat":
                self.send_error(404)
                return
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            customer_id = verify_token(token, secret) if token else None
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 8192:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict) or not isinstance(payload.get("question"), str):
                    raise ValueError("question must be a string")
                order_id = payload.get("order_id")
                if order_id is not None and (not isinstance(order_id, str) or len(order_id) > 64):
                    raise ValueError("Invalid order_id")
            except (ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {"error": "Invalid request"})
                return
            reply = agent.answer(payload["question"], customer_id, order_id)
            self._json(200, reply.to_dict())

        def do_GET(self) -> None:
            if self.path == "/health":
                self._json(200, {"status": "ok"})
            else:
                self.send_error(404)

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # Default access logging can include sensitive URL data; log nothing here.
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Listening on {host}:{port}", file=sys.stderr)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Grounded store support agent")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="Run a local demo question")
    ask.add_argument("question")
    ask.add_argument("--customer-id", help="Demo only: trusted identity")
    ask.add_argument("--order-id")
    token = sub.add_parser("token", help="Issue a demo storefront token")
    token.add_argument("customer_id")
    token.add_argument("--ttl", type=int, default=3600)
    api = sub.add_parser("serve", help="Start the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    sub.add_parser("eval", help="Run labeled evaluation cases")
    args = parser.parse_args()
    if args.command == "ask":
        print(json.dumps(make_agent().answer(args.question, args.customer_id,
                                             args.order_id).to_dict(), indent=2))
    elif args.command == "token":
        secret = os.getenv("SESSION_SECRET")
        if not secret:
            parser.error("SESSION_SECRET is required")
        print(issue_token(args.customer_id, secret, args.ttl))
    elif args.command == "serve":
        secret = os.getenv("SESSION_SECRET")
        if not secret or len(secret) < 32:
            parser.error("SESSION_SECRET must contain at least 32 characters")
        serve(make_agent(), args.host, args.port, secret)
    elif args.command == "eval":
        from .evaluate import evaluate
        raise SystemExit(evaluate(make_agent()))


if __name__ == "__main__":
    main()
