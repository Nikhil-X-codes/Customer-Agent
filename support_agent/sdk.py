"""In-process integration API for trusted company backends.

The host authenticates users and owns the operational source and handoff sink.
This module does not issue identities or include sample records.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from .agent import Reply, StatusProvider, SupportAgent
from .ai_router import Router
from .document_answer import DocumentAnswerer
from .knowledge import KnowledgeBase
from .supply import SupplyChainAgent, SupplyStore
from .supply_multi import SupplyMultiAgent
from .supply_llm import ProviderChain


@dataclass(frozen=True)
class AuthenticatedActor:
    """An ID obtained from the host's verified session, never from chat text."""

    subject: str

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject.strip() or len(self.subject) > 128:
            raise ValueError("Actor subject must be 1 to 128 nonblank characters")


class SupplySource(Protocol):
    """Read-only, current ERP/WMS/TMS adapter. Raise on errors; never cache stale rows."""

    def ready(self) -> bool: ...
    def inventory(self, sku: str) -> dict | None: ...
    def inventory_all(self) -> list[dict]: ...
    def purchase_order(self, identifier: str) -> dict | None: ...
    def shipment(self, identifier: str) -> dict | None: ...


class SupportSource(Protocol):
    """Read-only order and approved catalog adapter."""

    def ready(self) -> bool: ...
    def order_for_customer(self, order_id: str, customer_id: str) -> dict | None: ...
    def catalog(self) -> tuple[list[dict], list[dict]]: ...


class HandoffSink(Protocol):
    """Persist or dispatch a human review; return its externally visible ticket ID."""

    def enqueue(self, reply: Reply, actor_id: str | None = None) -> str: ...
    def ready(self) -> bool: ...


@dataclass(frozen=True)
class SDKResponse:
    outcome: str
    message: str
    reason: str
    sources: list[str]
    explanation: dict
    evidence: list[dict] | None
    agent_trace: list[str] | None
    handoff_status: str  # not_required | queued | unavailable
    ticket_id: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class AgentSDK:
    """Stable entry point for embedding the guarded agent in a trusted backend."""

    def __init__(self, agent: SupportAgent | SupplyChainAgent | SupplyMultiAgent,
                 source: SupplySource | SupportSource, handoffs: HandoffSink) -> None:
        if source is None or handoffs is None:
            raise ValueError("A source and handoff sink are required")
        if isinstance(source, SupplyStore) and not source.require_live:
            raise ValueError("SupplyStore must enable live-source checks for SDK use")
        if agent.data is not source:
            raise ValueError("Agent and readiness check must use the same source")
        self._agent, self._source, self._handoffs = agent, source, handoffs

    def ready(self) -> bool:
        """Check operational source and handoff availability for host health checks."""
        try:
            return bool(self._source.ready() and self._handoffs.ready())
        except Exception:
            return False

    @classmethod
    def for_supply_chain(cls, source: SupplySource, handoffs: HandoffSink, *,
                         knowledge: KnowledgeBase | None = None,
                         document_answerer: DocumentAnswerer | None = None,
                         providers: ProviderChain | None = None) -> AgentSDK:
        """Use a live source adapter; pass providers for Groq/OpenRouter tool calling."""
        if source is None or handoffs is None:
            raise ValueError("A source and handoff sink are required")
        base = SupplyChainAgent(source, knowledge=knowledge,
                                document_answerer=document_answerer)
        agent = SupplyMultiAgent(base, providers) if providers else base
        return cls(agent, source, handoffs)

    @classmethod
    def for_customer_support(cls, source: SupportSource, status: StatusProvider,
                             handoffs: HandoffSink, *,
                             router: Router | None = None,
                             knowledge: KnowledgeBase | None = None,
                             document_answerer: DocumentAnswerer | None = None) -> AgentSDK:
        if source is None or status is None or handoffs is None:
            raise ValueError("Source, status provider, and handoff sink are required")
        agent = SupportAgent(data=source, status_provider=status, router=router,
                             knowledge=knowledge,
                             document_answerer=document_answerer)
        return cls(agent, source, handoffs)

    def ask(self, question: str, *, actor: AuthenticatedActor | None = None,
            order_id: str | None = None) -> SDKResponse:
        if not isinstance(question, str):
            raise ValueError("question must be a string")
        if actor is not None and not isinstance(actor, AuthenticatedActor):
            raise TypeError("actor must be an AuthenticatedActor from the trusted host")
        if order_id is not None and (not isinstance(order_id, str) or len(order_id) > 64):
            raise ValueError("order_id must be a string of at most 64 characters")
        actor_id = actor.subject if actor else None
        try:
            # Supply guests must be rejected before any operational source access.
            if getattr(self._agent, "domain", "support") == "supply_chain" and actor is None:
                reply = self._agent.answer(question, None, order_id)
            elif not self._source.ready():
                reply = self._source_handoff(question, order_id)
            else:
                reply = self._agent.answer(question, actor_id, order_id)
        except Exception:
            # Company adapter errors must never escape as an unverified answer.
            reply = self._source_handoff(question, order_id)
        ticket_id = None
        handoff_status = "not_required"
        message = reply.message
        if reply.outcome == "handoff":
            try:
                ticket_id = self._handoffs.enqueue(reply, actor_id)
                if not isinstance(ticket_id, str) or not ticket_id.strip():
                    raise ValueError("Handoff sink returned no ticket ID")
                handoff_status = "queued"
            except Exception:
                handoff_status = "unavailable"
                ticket_id = None
                message = ("Human review is required, but the handoff could not be recorded. "
                           "Contact your support team directly.")
        fields = reply.to_dict()
        return SDKResponse(reply.outcome, message, reply.reason, list(reply.sources),
                           fields["explanation"], reply.evidence, reply.agent_trace,
                           handoff_status, ticket_id)

    @staticmethod
    def _source_handoff(question: str, order_id: str | None) -> Reply:
        return Reply("handoff", "I can’t verify current data. I’ll ask the team to review.",
                     "source_unavailable", [],
                     {"question": question[:500], "reason": "source_unavailable",
                      "order_id": order_id, "sources_checked": []})
