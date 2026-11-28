"""Conservative answer policy: source-backed response or explicit stop."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .ai_router import Router, RouterError
from .data import CatalogError, OrderLookupError, Status, StatusError, StoreData
from .document_answer import DocumentAnswerError, DocumentAnswerer, validate_decision
from .knowledge import KnowledgeBase, KnowledgeError


class StatusProvider(Protocol):
    def get(self, order_id: str) -> Status: ...


@dataclass(frozen=True)
class Reply:
    outcome: str  # answered | clarify | handoff
    message: str
    reason: str
    sources: list[str]
    handoff: dict | None = None
    evidence: list[dict] | None = None
    agent_trace: list[str] | None = None
    explanation: dict | None = None

    def to_dict(self) -> dict:
        result = asdict(self)
        if result["explanation"] is None:
            from .explain import explain
            result["explanation"] = explain(self.outcome, self.reason, self.sources,
                                            self.agent_trace)
        return result


HIGH_STAKES = re.compile(
    r"\b(refund|return(?!s?\s+(?:policy|window))|cancel|chargeback|dispute|fraud|stolen|lost|damaged|"
    r"wrong item|missing item|complaint|legal|lawsuit|change (?:my |the )?address|"
    r"change (?:my |the )?payment|exception|compensation|guarantee(?!\s+period)|"
    r"charged (?:me )?twice|double charged|unauthorized (?:charge|payment)|bank dispute)\b", re.I)
ORDER_WORDS = re.compile(r"\b(order|shipping|shipment|delivery|delivered|tracking|package|parcel|arrive|where is)\b", re.I)
PERSONAL_ORDER = re.compile(r"\b(my order|order status|tracking|where is|my package|my parcel|status of)\b", re.I)
ORDER_ID = re.compile(r"\b(?:ORD-)?\d{4,12}\b", re.I)
SAFE_STATES = {
    "processing": "being processed",
    "shipped": "shipped",
    "in transit": "in transit",
    "out for delivery": "out for delivery",
    "delivered": "marked delivered",
}
STOP_WORDS = {"a", "an", "and", "are", "about", "can", "do", "does", "for", "how",
              "i", "is", "it", "me", "my", "of", "on", "the", "to", "what", "with",
              "your", "you", "item", "product", "store", "tell"}


def _tokens(value: str) -> set[str]:
    return {part.rstrip("s") for part in re.findall(r"[a-z0-9]+", value.lower())
            if part not in STOP_WORDS}


def _candidate_sources(question: str, sources: list[dict], limit: int = 40) -> list[dict]:
    if len(sources) <= limit:
        return sources
    query = _tokens(question)
    scored = []
    for source in sources:
        title_terms = _tokens(source["title"] + " " + " ".join(source.get("terms", [])))
        answer_terms = _tokens(source["answer"])
        score = 4 * len(query & title_terms) + len(query & answer_terms)
        if score:
            scored.append((score, source))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [source for _, source in scored[:limit]]


class SupportAgent:
    def __init__(self, data: StoreData | None = None,
                 status_provider: StatusProvider | None = None,
                 max_status_age: timedelta = timedelta(minutes=15),
                 router: Router | None = None,
                 knowledge: KnowledgeBase | None = None,
                 document_answerer: DocumentAnswerer | None = None) -> None:
        if data is None:
            raise ValueError("An explicit operational data source is required")
        self.data = data
        self.status_provider = status_provider
        self.max_status_age = max_status_age
        self.router = router
        self.knowledge = knowledge
        self.document_answerer = document_answerer

    @staticmethod
    def _handoff(message: str, reason: str, question: str,
                 sources: list[str] | None = None, order_id: str | None = None) -> Reply:
        # The question may contain PII; the receiving support queue must protect it.
        return Reply("handoff", message, reason, sources or [],
                     {"question": question[:500], "reason": reason,
                      "order_id": order_id, "sources_checked": sources or []})

    def answer(self, question: str, customer_id: str | None = None,
               order_id: str | None = None) -> Reply:
        question = question.strip()
        if not question:
            return Reply("clarify", "What can I help you with?", "empty_question", [])
        if len(question) > 2000:
            return self._handoff("I’ll connect you with our team to help with this request.",
                                 "question_too_long", question)
        if HIGH_STAKES.search(question):
            return self._handoff("I’ll connect you with a support specialist for this request.",
                                 "high_stakes_request", question, order_id=order_id)

        if (order_id or ORDER_ID.search(question)) and ORDER_WORDS.search(question):
            return self._answer_order(question, customer_id, order_id)
        if PERSONAL_ORDER.search(question) and not re.search(r"\bshipping policy\b", question, re.I):
            return self._answer_order(question, customer_id, order_id)

        document_reply = self._answer_from_knowledge(question)
        if document_reply:
            return document_reply

        try:
            policies, products = self.data.catalog()
        except CatalogError:
            return self._handoff("I can’t verify store information right now. I’ll ask our team to check.",
                                 "catalog_unavailable", question)

        if self.router:
            sources = ([{"id": f"policy:{p['id']}", "title": p["id"],
                         "terms": p.get("terms", []), "answer": p["answer"]}
                        for p in policies] +
                       [{"id": f"product:{p['id']}", "title": p["name"],
                         "terms": p.get("terms", []), "answer": p["answer"]}
                        for p in products])
            candidates = _candidate_sources(question, sources)
            try:
                route = self.router.route(question, candidates)
            except RouterError:
                return self._handoff("I can’t verify an answer right now. I’ll connect you with our team.",
                                     "ai_unavailable", question)
            if route.confidence < 0.8:
                return self._handoff("I’m not sure enough to answer. I’ll connect you with our team.",
                                     "ai_uncertain", question)
            if route.action == "handoff":
                return self._handoff("I’ll connect you with a support specialist for this request.",
                                     "high_stakes_request", question)
            if route.action == "order_status":
                return self._answer_order(question, customer_id, order_id)
            if route.action == "clarify":
                return Reply("clarify", "Could you clarify which product or policy you mean?",
                             "ai_clarify", [])
            if route.action == "source":
                if order_id or ORDER_ID.search(question):
                    return self._handoff("I’ll ask our team to review this order-related question.",
                                         "ambiguous_order_context", question)
                record = next((s for s in candidates if s["id"] == route.source_id), None)
                if not record:
                    return self._handoff("I can’t verify that answer. I’ll connect you with our team.",
                                         "ai_invalid_source", question)
                return Reply("answered", record["answer"], "ai_source_match", [record["id"]])
            return self._handoff("I’m not sure from the information available. I’ll connect you with our team.",
                                 "no_verified_answer", question)

        policy_query = bool(re.search(
            r"\b(?:shipping (?:policy|cost|fee|options)|(?:returns? (?:policy|window))|"
            r"warranty policy|how much is shipping)\b", question, re.I))
        if (ORDER_WORDS.search(question) or order_id) and not (policy_query and not order_id):
            return self._answer_order(question, customer_id, order_id)

        matched = []
        lowered = question.lower()
        for policy in policies:
            if any(re.search(r"\b" + re.escape(term.lower()) + r"\b", lowered)
                   for term in policy["terms"]):
                matched.append((policy["id"], policy["answer"], "policy"))
        for product in products:
            if product["name"].lower() in lowered or any(
                    re.search(r"\b" + re.escape(term.lower()) + r"\b", lowered)
                    for term in product["terms"]):
                matched.append((product["id"], product["answer"], "product"))

        if len(matched) == 1:
            key, answer, kind = matched[0]
            return Reply("answered", answer, "source_match", [f"{kind}:{key}"])
        if len(matched) > 1:
            return Reply("clarify", "Which product or policy would you like to ask about?",
                         "multiple_topics", [f"{kind}:{key}" for key, _, kind in matched])
        return self._handoff("I’m not sure from the information available. I’ll connect you with our team.",
                             "no_verified_answer", question)

    def _answer_from_knowledge(self, question: str) -> Reply | None:
        if self.knowledge:
            try:
                passages = self.knowledge.search(question)
            except KnowledgeError:
                return self._handoff("I can’t check our documents right now. I’ll ask our team.",
                                     "knowledge_unavailable", question)
            if passages:
                if self.document_answerer:
                    try:
                        decision = self.document_answerer.answer(question, passages)
                        decision = validate_decision({"action": decision.action,
                                                      "answer": decision.answer,
                                                      "citations": decision.citations,
                                                      "confidence": decision.confidence}, passages)
                    except (DocumentAnswerError, ValueError, KeyError, TypeError):
                        return self._handoff("I can’t verify a document answer. I’ll ask our team.",
                                             "document_answer_unavailable", question)
                    if decision.action != "answer":
                        return self._handoff("Our documents need a closer review. I’ll ask our team.",
                                             "document_needs_review", question)
                    by_id = {passage.chunk_id: passage for passage in passages}
                    evidence = [{"document_id": by_id[c["chunk_id"]].document_id,
                                 "title": by_id[c["chunk_id"]].title,
                                 "chunk_id": c["chunk_id"], "quote": c["quote"]}
                                for c in decision.citations]
                    sources = [f"document:{item['document_id']}:{item['chunk_id']}"
                               for item in evidence]
                    verified_text = " ".join(item["quote"] for item in evidence)
                    return Reply("answered", verified_text, "document_answer", sources,
                                 evidence=evidence)
                passage = passages[0]
                quote = passage.text[:450]
                return Reply("answered", f"A relevant passage from {passage.title} says: “{quote}”",
                             "document_excerpt",
                             [f"document:{passage.document_id}:{passage.chunk_id}"],
                             evidence=[{"document_id": passage.document_id,
                                        "title": passage.title, "chunk_id": passage.chunk_id,
                                        "quote": quote}])

        return None

    def _answer_order(self, question: str, customer_id: str | None,
                      order_id: str | None) -> Reply:
        if not customer_id:
            return Reply("clarify", "Please sign in to check your order status.",
                         "authentication_required", [])
        if not order_id:
            match = ORDER_ID.search(question)
            order_id = match.group(0).upper() if match else None
        if not order_id:
            return Reply("clarify", "Please provide your order number.",
                         "order_id_required", [])
        order_id = order_id.upper()
        if order_id.isdigit():
            order_id = f"ORD-{order_id}"
        try:
            order = self.data.order_for_customer(order_id, customer_id)
        except OrderLookupError:
            return self._handoff("I can’t verify your order right now. I’ll ask our team to check.",
                                 "order_lookup_unavailable", question, order_id=order_id)
        if (not isinstance(order, dict) or order.get("id") != order_id or
                order.get("customer_id") != customer_id):
            # Same response for nonexistent and another customer's orders.
            return Reply("clarify", "I couldn’t find that order in your account. Please check the number.",
                         "order_not_found_for_customer", [])
        sources = [f"orders:{order_id}"]
        if self.status_provider is None:
            return self._handoff("I can’t verify the latest status right now. I’ll ask our team to check.",
                                 "status_unavailable", question, sources, order_id)
        try:
            status = self.status_provider.get(order_id)
        except StatusError:
            return self._handoff("I can’t verify the latest status right now. I’ll ask our team to check.",
                                 "status_unavailable", question, sources, order_id)
        if (not isinstance(status, Status) or status.order_id != order_id or
                not isinstance(status.observed_at, datetime) or
                status.observed_at.utcoffset() is None or
                not isinstance(status.source, str) or not 1 <= len(status.source) <= 64):
            return self._handoff("I can’t verify the latest status right now. I’ll ask our team to check.",
                                 "status_unavailable", question, sources, order_id)
        now = datetime.now(timezone.utc)
        age = now - status.observed_at.astimezone(timezone.utc)
        sources.append(f"{status.source}:{order_id}")
        if age > self.max_status_age or age < -timedelta(minutes=1):
            return self._handoff("I can’t verify a current status. I’ll ask our team to check.",
                                 "status_stale", question, sources, order_id)
        if order["state"] != status.state:
            return self._handoff("Our records show different statuses. I’ll ask our team to check your order.",
                                 "status_conflict", question, sources, order_id)
        if status.state not in SAFE_STATES:
            return self._handoff("I can’t verify a routine status for this order. I’ll ask our team to check.",
                                 "status_needs_review", question, sources, order_id)
        return Reply("answered", f"Order {order_id} is {SAFE_STATES[status.state]}.",
                     "verified_status", sources)
