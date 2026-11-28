"""Public decision summaries derived from verified outcomes, never model reasoning."""

CHECKS = {
    "inventory_snapshot": ["Record timestamp within 24 hours", "Nonnegative stock values", "Available = on hand − reserved"],
    "low_stock_check": ["All returned records within 24 hours", "Available stock compared with reorder point"],
    "purchase_order_snapshot": ["Record timestamp within 24 hours", "Recognized state and valid quantity", "Expected date not overdue"],
    "shipment_snapshot": ["Status timestamp within 2 hours", "Recognized shipment state", "ETA not overdue; ETA is an estimate"],
    "document_answer": ["Answer checked against cited approved document text"],
    "verified_status": ["Signed session matched order owner", "Status timestamp and order state checked"],
}


def explain(outcome: str, reason: str, sources: list[str],
            agent_trace: list[str] | None = None) -> dict:
    """Expose what the application verified, without chain of thought or secrets."""
    if outcome == "answered":
        summary = "Answered from the cited source after the listed checks."
    elif outcome == "handoff":
        summary = "A reliable answer or safe action could not be verified; a person must review it."
    else:
        summary = "More information or an authorized session is needed before checking records."
    return {"decision": outcome, "reason_code": reason, "summary": summary,
            "checks": CHECKS.get(reason, []), "source_ids": list(sources),
            "agent_steps": list(agent_trace or [])}
