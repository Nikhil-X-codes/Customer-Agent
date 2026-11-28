"""Labeled local evaluation; hidden test accuracy must be measured separately."""

from __future__ import annotations

import json
from pathlib import Path

from .agent import SupportAgent


def evaluate(agent: SupportAgent, path: Path) -> int:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Evaluation cases must be a non-empty list")
    passed = 0
    handoff_tp = handoff_fp = handoff_fn = 0
    for case in cases:
        reply = agent.answer(case["question"], case.get("customer_id"), case.get("order_id"))
        good = (reply.outcome == case["outcome"] and reply.reason == case["reason"]
                and ("source" not in case or case["source"] in reply.sources)
                and ("message_contains" not in case or
                     case["message_contains"] in reply.message))
        passed += good
        if reply.outcome == "handoff" and case["outcome"] == "handoff":
            handoff_tp += 1
        elif reply.outcome == "handoff":
            handoff_fp += 1
        elif case["outcome"] == "handoff":
            handoff_fn += 1
        print(f"{'PASS' if good else 'FAIL'} {case['id']}: {reply.outcome}/{reply.reason}")
    precision = handoff_tp / (handoff_tp + handoff_fp) if handoff_tp + handoff_fp else 0
    recall = handoff_tp / (handoff_tp + handoff_fn) if handoff_tp + handoff_fn else 0
    print(f"Case accuracy: {passed}/{len(cases)} ({passed / len(cases):.1%})")
    print(f"Handoff precision: {precision:.1%}; recall: {recall:.1%}")
    return 0 if passed == len(cases) else 1
