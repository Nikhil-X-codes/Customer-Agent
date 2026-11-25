"""Conservative answer policy: source-backed response or explicit stop."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .data import FixtureStatusProvider, Status, StatusError, StoreData


class StatusProvider(Protocol):
    def get(self, order_id: str) -> Status: ...


@dataclass(frozen=True)
class Reply:
    outcome: str  # answered | clarify | handoff
    message: str
    reason: str
    sources: list[str]
    handoff: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


HIGH_STAKES = re.compile(
    r"\b(refund|return(?!s?\s+policy)|cancel|chargeback|dispute|fraud|stolen|lost|damaged|"
    r"wrong item|missing item|complaint|legal|lawsuit|change (?:my |the )?address|"
    r"change (?:my |the )?payment|exception|compensation|guarantee(?!\s+period))\b", re.I)
ORDER_WORDS = re.compile(r"\b(order|shipping|shipment|delivery|delivered|tracking|package|parcel|arrive|where is)\b", re.I)
ORDER_ID = re.compile(r"\b(?:ORD-)?\d{4,12}\b", re.I)


class SupportAgent:
    def __init__(self, data: StoreData | None = None,
                 status_provider: StatusProvider | None = None,
                 max_status_age: timedelta = timedelta(minutes=15)) -> None:
        self.data = data or StoreData()
        self.status_provider = status_provider or FixtureStatusProvider()
        self.max_status_age = max_status_age

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

        policy_query = bool(re.search(r"\b(shipping|returns?|warranty) policy\b", question, re.I))
        if (ORDER_WORDS.search(question) or order_id) and not (policy_query and not order_id):
            return self._answer_order(question, customer_id, order_id)

        matched = []
        lowered = question.lower()
        for policy in self.data.policies:
            if any(re.search(r"\b" + re.escape(term.lower()) + r"\b", lowered)
                   for term in policy["terms"]):
                matched.append((policy["id"], policy["answer"], "policy"))
        for product in self.data.products:
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
        order = self.data.order_for_customer(order_id, customer_id)
        if not order:
            # Same response for nonexistent and another customer's orders.
            return Reply("clarify", "I couldn’t find that order in your account. Please check the number.",
                         "order_not_found_for_customer", [])
        sources = [f"orders:{order_id}"]
        try:
            status = self.status_provider.get(order_id)
        except StatusError:
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
        detail = f" {status.detail}" if status.detail else ""
        return Reply("answered", f"Order {order_id} is {status.state}.{detail}",
                     "verified_status", sources)
