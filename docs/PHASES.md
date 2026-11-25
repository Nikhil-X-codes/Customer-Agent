# Delivery phases

## Phase 1 — Safe runnable baseline (complete)

- [x] Define answer, clarification, and handoff outcomes.
- [x] Add fictional policy, product, order, and status sources.
- [x] Enforce ownership before status lookup.
- [x] Hand off high-stakes requests and uncertain answers.
- [x] Add CLI, HTTP interface, labeled evaluation, tests, README, and security guide.

Exit check: local tests pass and each answer names a source.

## Phase 2 — Real store integration (requires store systems)

- [ ] Replace demo policy and product JSON with versioned, approved store content.
- [ ] Replace demo order JSON with a read-only order table adapter.
- [ ] Connect the HTTPS live status endpoint and map its state vocabulary.
- [ ] Integrate storefront sign-in with short-lived customer tokens.
- [ ] Deliver structured handoffs to the human support queue.

Exit check: authorized users see only their own orders; every status answer agrees with a fresh upstream record; a human receives a complete handoff.

## Phase 3 — Accuracy and reliability (requires representative questions)

- [ ] Create a held-out set from real, redacted customer questions with expected answer and routing labels.
- [ ] Measure answer correctness, unsafe answer rate, handoff recall and precision, and latency.
- [ ] Expand paraphrase handling only with source-backed answers and regression cases.
- [ ] Test timeouts, stale timestamps, mismatched IDs, conflicting states, and prompt injection attempts.

Exit check: agreed accuracy and safety thresholds pass on a held-out set; the conflict case still hands off.

## Phase 4 — Launch and operations (requires deployment environment)

- [ ] Put the API behind TLS, authentication, rate limits, and monitoring.
- [ ] Configure secrets, audit access, retention, and incident response.
- [ ] Start with a small traffic slice and review handoffs and wrong answers daily.
- [ ] Set an owner and review cadence for policy and catalog changes.

Exit check: support staff can process handoffs, and monitoring shows no unauthorized status disclosure or unhandled upstream failure.
