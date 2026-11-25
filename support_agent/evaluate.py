"""Labeled local evaluation; hidden test accuracy must be measured separately."""

from __future__ import annotations

import json
from pathlib import Path

from .agent import SupportAgent


def evaluate(agent: SupportAgent) -> int:
    path = Path(__file__).resolve().parent.parent / "eval" / "cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        reply = agent.answer(case["question"], case.get("customer_id"), case.get("order_id"))
        good = reply.outcome == case["outcome"] and reply.reason == case["reason"]
        passed += good
        print(f"{'PASS' if good else 'FAIL'} {case['id']}: {reply.outcome}/{reply.reason}")
    print(f"Accuracy: {passed}/{len(cases)} ({passed / len(cases):.1%})")
    return 0 if passed == len(cases) else 1
