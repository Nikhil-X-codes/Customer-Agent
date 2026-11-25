# Customer Agent

A conservative customer support agent for a store. It answers product and policy questions from curated store data, checks order ownership, reads current order status, and hands off when it cannot give a verified low-risk answer.

**This repository ships with fictional demo data.** Replace the files in `data/` and configure the live status API before using it for a real store. The local fixture provider is explicitly labeled `demo fixture` in responses.

## Quick start

Python 3.10+ is required. The core project has no runtime dependencies.

```bash
python -m support_agent.cli ask "What is your shipping policy?"
python -m support_agent.cli ask "Where is my order ORD-1001?" --customer-id cust-1
python -m support_agent.cli eval
python -m unittest discover -s tests -v
```

The `ask --customer-id` flag is a **local demo shortcut**. A public client must not choose its own customer ID.

## HTTP API

The API accepts a short-lived signed token issued by a trusted storefront backend. It reads the customer ID from the token, not the request body or question.

```bash
export SESSION_SECRET="replace-with-a-random-secret-of-at-least-32-characters"
python -m support_agent.cli serve
```

In a separate terminal, for **local testing only**:

```bash
export SESSION_SECRET="replace-with-a-random-secret-of-at-least-32-characters"
TOKEN=$(python -m support_agent.cli token cust-1)
curl -sS http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"question":"Where is order ORD-1001?"}'
```

`POST /chat` accepts `question` and optional `order_id`. It returns `outcome` (`answered`, `clarify`, or `handoff`), `message`, `reason`, `sources`, and an optional `handoff` object. `GET /health` is a basic process health check.

In production, terminate TLS at a trusted gateway, issue tokens only after storefront authentication, keep `SESSION_SECRET` and `STATUS_API_TOKEN` in a secret manager, and rate limit requests. The built-in server is a reference interface, not a production edge service.

## Sources and decision flow

```text
Customer question
  -> high-stakes rule? -> handoff
  -> order question? -> verified customer -> owned order -> status API
                                      -> current and consistent? -> answer
                                      -> missing/stale/conflicting? -> handoff
  -> one curated policy or product match? -> answer from that record
  -> multiple matches? -> clarify
  -> no verified match? -> handoff
```

The agent does not modify orders, issue refunds, promise delivery dates, or interpret unknown policies. Unknown requests are handed to a person. `sources` identifies the records used so an evaluator can inspect each answer.

### Live status integration

Set `STATUS_API_URL` to an HTTPS base URL and `STATUS_API_TOKEN` to its bearer token. The adapter requests `GET {STATUS_API_URL}/orders/{order_id}/status` and expects:

```json
{"order_id":"ORD-1001","state":"in transit","detail":"Carrier has the package.","observed_at":"2026-11-25T12:00:00Z"}
```

The upstream timestamp must be timezone aware and no more than 15 minutes old. The order table's `state` must agree with the live `state`; disagreement triggers handoff. `STATUS_API_URL` absent means the demo fixture is used. In a real integration, replace `StoreData`'s JSON order lookup with the store's database or API and make the status vocabulary consistent across systems.

## Evaluation

`python -m support_agent.cli eval` scores the labeled examples in `eval/cases.json` by outcome and reason. The current demo scores **10/10** on these local cases. This does **not** estimate hidden-test or production accuracy. Tests also cover access control, status conflicts, stale data, upstream errors, and signed tokens.

A failure handled well is built into the demo: `ORD-1003` belongs to `cust-1`; the order table says `shipped` while the status feed says `delivery exception`. The agent returns `handoff` with reason `status_conflict` and does not invent a delivery estimate.

Before launch, expand evaluation with real policy paraphrases, multiple product names, ambiguous orders, multilingual questions if supported, malicious input, and real upstream failures. Measure answer correctness, unsafe answer rate, handoff recall, handoff precision, and response latency. Review each incorrect confident answer before broadening coverage.

## Project layout

- `support_agent/agent.py`: answer and handoff policy
- `support_agent/data.py`: JSON store and live status adapter
- `support_agent/auth.py`: signed customer session token
- `support_agent/cli.py`: CLI and reference HTTP API
- `data/`: fictional demo sources
- `eval/`: labeled questions
- `tests/`: behavior and security tests
- `AGENT.md`: implementation rules and next steps
- `SECURITY.md`: threat model and deployment controls
- `docs/PHASES.md`: phased delivery plan

## Current limits

The source matcher uses explicit phrases and terms, so a novel paraphrase will usually hand off. This is an intentional safe baseline; hidden-test accuracy is unknown. There is no real storefront authentication, support queue, database, or live status service connected in this repository. The handoff object is a structured payload for a human-support integration, not a delivered support ticket. See [docs/PHASES.md](docs/PHASES.md) for the integration work.
