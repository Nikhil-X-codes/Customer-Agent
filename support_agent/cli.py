"""CLI and minimal HTTP entry points."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import time
import re
import uuid
from datetime import timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

from .agent import SupportAgent
from .ai_router import GroqRouter, OpenAIRouter
from .data import DynamicStoreData, HttpStatusProvider, SqliteStoreData
from .document_answer import GroqDocumentAnswerer
from .dotenv import load_dotenv
from .handoff import HandoffQueue
from .http_guard import LimitedHTTPServer, RequestLimiter
from .identity import IdentityVerifier, OIDCVerifier, SignedTokenVerifier
from .knowledge import KnowledgeBase, KnowledgeError, MAX_UPLOAD_BYTES
from .supply import SupplyChainAgent, SupplyDataError, SupplyStore
from .supply_http import HttpSupplyStore
from .supply_llm import configured_chain
from .supply_multi import SupplyMultiAgent


WEB_DIR = Path(__file__).resolve().parent / "web"


def _collection_name(domain: str, mode: str) -> str:
    default = f"{domain}_live_knowledge_v1"
    name = os.getenv("QDRANT_COLLECTION", default)
    required_prefix = f"{domain}_live_"
    if not name.startswith(required_prefix):
        raise ValueError("Qdrant collection must match the selected domain and mode")
    return name


def make_agent() -> SupportAgent:
    mode = os.getenv("APP_MODE", "production")
    if mode != "production":
        raise ValueError("Only APP_MODE=production is supported")
    domain = os.getenv("AGENT_DOMAIN", "supply_chain")
    if domain == "supply_chain":
        supply_path = os.getenv("SUPPLY_DB_PATH", "")
        api_url = os.getenv("SUPPLY_API_URL")
        if bool(supply_path) == bool(api_url):
            raise ValueError("Production requires exactly one of SUPPLY_DB_PATH or SUPPLY_API_URL")
        sync_minutes = int(os.getenv("SUPPLY_MAX_SYNC_AGE_MINUTES", "30"))
        if not 1 <= sync_minutes <= 1440:
            raise ValueError("SUPPLY_MAX_SYNC_AGE_MINUTES must be 1 to 1440")
        max_sync_age = timedelta(minutes=sync_minutes)
        if api_url:
            store = HttpSupplyStore(api_url, os.getenv("SUPPLY_API_TOKEN", ""),
                                    max_sync_age=max_sync_age)
        else:
            path = Path(supply_path)
            if not path.is_absolute():
                raise ValueError("Production SUPPLY_DB_PATH must be absolute")
            store = SupplyStore(path, require_live=True,
                                max_sync_age=max_sync_age)
        if not store.ready():
            raise SupplyDataError("Real supply source is unavailable or invalid")
        ai_mode = os.getenv("AI_MODE", "off")
        qdrant_url = os.getenv("QDRANT_URL")
        if ai_mode not in ("off", "groq"):
            raise ValueError("Supply chain mode supports AI_MODE=off or groq")
        if mode == "production" and qdrant_url and not qdrant_url.startswith("https://"):
            raise ValueError("Production QDRANT_URL must use HTTPS")
        if mode == "production" and qdrant_url and ai_mode != "groq":
            raise ValueError("Production document answering requires AI_MODE=groq")
        knowledge = (KnowledgeBase(url=qdrant_url, api_key=os.getenv("QDRANT_API_KEY") or None,
                                   collection=_collection_name(domain, mode))
                     if qdrant_url else None)
        answerer = (GroqDocumentAnswerer(os.getenv("GROQ_API_KEY", ""),
                                         os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
                                         domain="supply_chain")
                    if knowledge and ai_mode == "groq" else None)
        base = SupplyChainAgent(store, knowledge=knowledge,
                                document_answerer=answerer)
        agent_mode = os.getenv("SUPPLY_AGENT_MODE", "deterministic")
        if agent_mode == "deterministic":
            return base
        if agent_mode != "multi" or ai_mode != "groq":
            raise ValueError("SUPPLY_AGENT_MODE=multi requires AI_MODE=groq")
        if not os.getenv("OPENROUTER_API_KEY"):
            raise ValueError("SUPPLY_AGENT_MODE=multi requires OPENROUTER_API_KEY")
        chain = configured_chain(os.getenv("GROQ_API_KEY", ""),
                                 os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
                                 os.getenv("OPENROUTER_API_KEY", ""),
                                 os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b"))
        return SupplyMultiAgent(base, chain)
    if domain != "support":
        raise ValueError("AGENT_DOMAIN must be support or supply_chain")
    order_db = os.getenv("ORDER_DB_PATH")
    catalog_db = os.getenv("CATALOG_DB_PATH")
    qdrant_url = os.getenv("QDRANT_URL")
    knowledge_configured = bool(qdrant_url)
    base_url = os.getenv("STATUS_API_URL")
    if mode == "production" and (not order_db or not (catalog_db or knowledge_configured) or not base_url or
                                  not os.getenv("STATUS_API_TOKEN")):
        raise ValueError("Production requires ORDER_DB_PATH, STATUS_API_URL, STATUS_API_TOKEN, and CATALOG_DB_PATH or QDRANT_URL")
    if mode == "production" and qdrant_url and not qdrant_url.startswith("https://"):
        raise ValueError("Production QDRANT_URL must use HTTPS")
    if catalog_db and not order_db:
        raise ValueError("CATALOG_DB_PATH requires ORDER_DB_PATH")
    data = (DynamicStoreData(Path(order_db), Path(catalog_db)) if catalog_db else
            SqliteStoreData(Path(order_db)))
    ai_mode = os.getenv("AI_MODE", "off")
    if ai_mode not in ("off", "openai", "groq"):
        raise ValueError("AI_MODE must be off, openai, or groq")
    if ai_mode == "openai":
        router = OpenAIRouter(os.getenv("OPENAI_API_KEY", ""),
                              os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    elif ai_mode == "groq":
        router = GroqRouter(os.getenv("GROQ_API_KEY", ""),
                            os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    else:
        router = None
    knowledge = (KnowledgeBase(url=qdrant_url, api_key=os.getenv("QDRANT_API_KEY") or None,
                               collection=_collection_name(domain, mode))
                 if knowledge_configured else None)
    if mode == "production" and knowledge and ai_mode != "groq":
        raise ValueError("Production document answering requires AI_MODE=groq")
    document_answerer = (GroqDocumentAnswerer(os.getenv("GROQ_API_KEY", ""),
                                               os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
                         if knowledge and ai_mode == "groq" else None)
    if base_url:
        return SupportAgent(data=data, status_provider=HttpStatusProvider(
            base_url, os.getenv("STATUS_API_TOKEN", "")), router=router,
            knowledge=knowledge, document_answerer=document_answerer)
    return SupportAgent(data=data, router=router, knowledge=knowledge,
                        document_answerer=document_answerer)


def build_handler(agent: SupportAgent, secret: str, queue: HandoffQueue,
                  admin_key: str | None = None, *,
                  verifier: IdentityVerifier | None = None,
                  limiter: RequestLimiter | None = None,
                  chat_limit: int = 30, preauth_limit: int = 120):
    identity = verifier or SignedTokenVerifier(secret)
    rate = limiter or RequestLimiter()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.request_id = uuid.uuid4().hex
            if self.path == "/admin/documents":
                if not self._admin_allowed():
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 1 or size > MAX_UPLOAD_BYTES:
                        raise KnowledgeError("Document must be 1 byte to 5 MB")
                    name = unquote(self.headers.get("X-Document-Name", ""))
                    document = agent.knowledge.add(name, self.rfile.read(size))
                except (ValueError, KnowledgeError) as exc:
                    self._json(400, {"error": str(exc)})
                    return
                except TimeoutError:
                    self._json(408, {"error": "Request timed out"})
                    return
                except Exception:
                    self._json(503, {"error": "Document store unavailable"})
                    return
                self._json(201, document)
                return
            streaming = self.path == "/chat/stream"
            if self.path not in ("/chat", "/chat/stream"):
                self.send_error(404)
                return
            started = time.perf_counter()
            ip = self.client_address[0]
            if not rate.allow(f"preauth:{ip}", preauth_limit):
                self._json(429, {"error": "Too many requests"})
                return
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            try:
                customer_id = identity.verify(token) if token else None
            except Exception:
                customer_id = None
            if token and not customer_id:
                self._json(401, {"error": "Invalid or expired session"})
                return
            if not rate.allow(f"chat:{ip}:{customer_id or 'guest'}", chat_limit):
                self._json(429, {"error": "Too many requests"})
                return
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
            except TimeoutError:
                self._json(408, {"error": "Request timed out"})
                return
            except (ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {"error": "Invalid request"})
                return
            try:
                if streaming:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Request-ID", self.request_id)
                    self.end_headers()
                    self._event("progress", {"message": "Checking approved sources…"})
                try:
                    reply = agent.answer(payload["question"], customer_id, order_id)
                except Exception:
                    reply = SupportAgent._handoff(
                        "I can’t verify the answer right now. Please contact the team.",
                        "agent_unavailable", payload["question"], order_id=order_id)
                result = reply.to_dict()
                if reply.outcome == "handoff":
                    try:
                        ticket_id = queue.enqueue(reply, customer_id)
                    except Exception:
                        failure = "Support handoff unavailable. Please contact support directly."
                        self._audit("error", "handoff_unavailable", started)
                        if streaming:
                            self._event("error", {"message": failure})
                        else:
                            self._json(503, {"error": failure})
                        return
                    result["ticket_id"] = ticket_id
                    result.pop("handoff")
                self._audit(reply.outcome, reply.reason, started)
                if streaming:
                    self._event("meta", {key: value for key, value in result.items()
                                         if key not in ("message", "handoff")})
                    for start in range(0, len(reply.message), 256):
                        self._event("chunk", {"text": reply.message[start:start + 256]})
                    self._event("done", {})
                else:
                    self._json(200, result)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_GET(self) -> None:
            static_files = {"/": ("index.html", "text/html; charset=utf-8"),
                            "/app.css": ("app.css", "text/css; charset=utf-8"),
                            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                            "/admin": ("admin.html", "text/html; charset=utf-8"),
                            "/admin.js": ("admin.js", "text/javascript; charset=utf-8")}
            if self.path in static_files:
                name, content_type = static_files[self.path]
                body = (WEB_DIR / name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                                 "style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                                 "base-uri 'none'; frame-ancestors 'none'")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/ui/config":
                self._json(200, {"knowledge_enabled": agent.knowledge is not None,
                                 "domain": getattr(agent, "domain", "support")})
            elif self.path == "/admin/documents":
                if not self._admin_allowed():
                    return
                try:
                    self._json(200, {"documents": agent.knowledge.list_documents()})
                except Exception:
                    self._json(503, {"error": "Knowledge database unavailable"})
            elif self.path == "/health":
                self._json(200, {"status": "ok"})
            elif self.path == "/ready":
                try:
                    ready = queue.ready() and agent.data.ready() and (
                        agent.knowledge is None or agent.knowledge.ready())
                except Exception:
                    ready = False
                self._json(200 if ready else 503, {"status": "ready" if ready else "unavailable"})
            else:
                self.send_error(404)

        def do_DELETE(self) -> None:
            if not self.path.startswith("/admin/documents/"):
                self.send_error(404)
                return
            if not self._admin_allowed():
                return
            document_id = self.path.removeprefix("/admin/documents/")
            if not re.fullmatch(r"[0-9a-f]{32}", document_id):
                self._json(400, {"error": "Invalid document ID"})
                return
            try:
                deleted = agent.knowledge.delete(document_id)
            except Exception:
                self._json(503, {"error": "Knowledge database unavailable"})
                return
            self._json(200 if deleted else 404, {"deleted": deleted})

        def _admin_allowed(self) -> bool:
            if not rate.allow(f"admin:{self.client_address[0]}", 30):
                self._json(429, {"error": "Too many requests"})
                return False
            if not agent.knowledge or not admin_key:
                self.send_error(404)
                return False
            provided = self.headers.get("X-Admin-Key", "")
            if not hmac.compare_digest(provided, admin_key):
                self._json(403, {"error": "Admin access required"})
                return False
            return True

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 429:
                self.send_header("Retry-After", "60")
            if getattr(self, "request_id", None):
                self.send_header("X-Request-ID", self.request_id)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _event(self, event: str, payload: dict) -> None:
            body = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            self.wfile.write(body)
            self.wfile.flush()

        def _audit(self, outcome: str, reason: str, started: float) -> None:
            # Do not log questions, tokens, actor IDs, or source rows.
            print(json.dumps({"event": "agent_decision", "request_id": self.request_id,
                              "domain": getattr(agent, "domain", "support"),
                              "outcome": outcome, "reason": reason,
                              "elapsed_ms": round((time.perf_counter() - started) * 1000)}),
                  file=sys.stderr)

        def log_message(self, format: str, *args: object) -> None:
            # Default access logging can include sensitive URL data; log nothing here.
            pass

    return Handler


def serve(agent: SupportAgent, host: str, port: int, secret: str,
          queue: HandoffQueue, verifier: IdentityVerifier | None = None) -> None:
    admin_key = os.getenv("ADMIN_API_KEY")
    workers = int(os.getenv("HTTP_MAX_WORKERS", "32"))
    chat_limit = int(os.getenv("CHAT_RATE_LIMIT_PER_MINUTE", "30"))
    if not 1 <= workers <= 256 or not 1 <= chat_limit <= 1000:
        raise ValueError("HTTP_MAX_WORKERS or CHAT_RATE_LIMIT_PER_MINUTE is out of range")
    server = LimitedHTTPServer((host, port), build_handler(
        agent, secret, queue, admin_key, verifier=verifier, chat_limit=chat_limit), workers)
    print(f"Listening on {host}:{port}", file=sys.stderr)
    server.serve_forever()


def configured_identity(secret: str, domain: str) -> IdentityVerifier:
    mode = os.getenv("AUTH_MODE", "signed")
    if mode == "signed":
        return SignedTokenVerifier(secret)
    if mode != "oidc":
        raise ValueError("AUTH_MODE must be signed or oidc")
    role = os.getenv("OIDC_REQUIRED_ROLE")
    if domain == "supply_chain" and not role:
        raise ValueError("Supply OIDC requires OIDC_REQUIRED_ROLE")
    return OIDCVerifier(os.getenv("OIDC_ISSUER", ""), os.getenv("OIDC_AUDIENCE", ""),
                        os.getenv("OIDC_JWKS_URL", ""), required_role=role,
                        role_claim=os.getenv("OIDC_ROLE_CLAIM", "roles"))


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Grounded store support agent")
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("serve", help="Start the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    tickets = sub.add_parser("handoffs", help="Manage local human support queue")
    tickets.add_argument("action", choices=("list", "close"))
    tickets.add_argument("ticket_id", nargs="?")
    evaluation = sub.add_parser("eval", help="Run labeled evaluation cases")
    evaluation.add_argument("--cases", type=Path, required=True)
    sub.add_parser("doctor", help="Check configuration and source readiness without printing secrets")
    args = parser.parse_args()
    if args.command == "serve":
        app_mode = os.getenv("APP_MODE")
        if app_mode != "production":
            parser.error("Set APP_MODE=production before serving")
        secret = os.getenv("SESSION_SECRET")
        try:
            verifier = configured_identity(secret or "", os.getenv("AGENT_DOMAIN", "supply_chain"))
        except (ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        if app_mode == "production" and not os.getenv("HANDOFF_DB_PATH"):
            parser.error("Production requires HANDOFF_DB_PATH")
        if app_mode == "production" and not Path(os.getenv("HANDOFF_DB_PATH", "")).is_absolute():
            parser.error("Production HANDOFF_DB_PATH must be absolute")
        if app_mode == "production" and os.getenv("QDRANT_URL"):
            admin_key = os.getenv("ADMIN_API_KEY", "")
            if len(admin_key) < 32 or admin_key.startswith("replace-"):
                parser.error("Production document uploads require a real ADMIN_API_KEY")
        queue_path = Path(os.getenv("HANDOFF_DB_PATH", "var/handoffs.db"))
        try:
            agent = make_agent()
        except (ValueError, FileNotFoundError, SupplyDataError) as exc:
            parser.error(str(exc))
        try:
            serve(agent, args.host, args.port, secret or "", HandoffQueue(queue_path), verifier)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    elif args.command == "handoffs":
        queue = HandoffQueue(Path(os.getenv("HANDOFF_DB_PATH", "var/handoffs.db")))
        if args.action == "list":
            print(json.dumps(queue.list_open(), indent=2))
        elif not args.ticket_id:
            parser.error("ticket_id required to close a handoff")
        else:
            print("closed" if queue.close(args.ticket_id) else "not found")
    elif args.command == "eval":
        from .evaluate import evaluate
        raise SystemExit(evaluate(make_agent(), args.cases))
    elif args.command == "doctor":
        try:
            if os.getenv("APP_MODE") != "production":
                raise ValueError("APP_MODE=production is required")
            configured_identity(os.getenv("SESSION_SECRET", ""),
                                os.getenv("AGENT_DOMAIN", "supply_chain"))
            handoff_path = Path(os.getenv("HANDOFF_DB_PATH", ""))
            if not os.getenv("HANDOFF_DB_PATH") or not handoff_path.is_absolute():
                raise ValueError("HANDOFF_DB_PATH must be an absolute persistent path")
            if os.getenv("QDRANT_URL"):
                admin_key = os.getenv("ADMIN_API_KEY", "")
                if len(admin_key) < 32 or admin_key.startswith("replace-"):
                    raise ValueError("Document uploads require a real ADMIN_API_KEY")
            if not HandoffQueue(handoff_path).ready():
                raise ValueError("Human handoff database is unavailable")
            agent = make_agent()
            if not agent.data.ready():
                raise ValueError("Order or catalog database is unavailable or invalid")
            if agent.knowledge and not agent.knowledge.ready():
                raise ValueError("Knowledge database is unavailable")
        except (ValueError, FileNotFoundError, OSError, RuntimeError, SupplyDataError) as exc:
            print(json.dumps({"status": "invalid", "reason": str(exc)}))
            raise SystemExit(1)
        print(json.dumps({"status": "configured",
                          "ai_mode": os.getenv("AI_MODE", "off"),
                          "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
                          if os.getenv("AI_MODE") == "groq" else
                          os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
                          if os.getenv("AI_MODE") == "openai" else None,
                          "order_store": type(agent.data).__name__,
            "status_provider": (type(agent.status_provider).__name__
                                if getattr(agent, "domain", "support") == "support" else None),
            "domain": getattr(agent, "domain", "support"),
            "supply_agent_mode": (os.getenv("SUPPLY_AGENT_MODE", "deterministic")
                                  if getattr(agent, "domain", "support") == "supply_chain" else None),
            "openrouter_fallback_configured": bool(os.getenv("OPENROUTER_API_KEY"))
            if getattr(agent, "domain", "support") == "supply_chain" else None}, indent=2))


if __name__ == "__main__":
    main()
