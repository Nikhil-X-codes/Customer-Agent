# Embed the agent in a company backend

Install this repository or its wheel in the same trusted Python environment as your application:

```bash
python -m pip install /path/to/customer-agent
```

The public API is `AgentSDK`, `AuthenticatedActor`, and `SDKResponse` from `support_agent`. The SDK runs **in process**. It does not start the bundled HTTP server, load `.env`, issue tokens, or include company records. Use a backend process, never a browser or mobile client, so service tokens and document access remain private.

## Supply chain integration

```python
from support_agent import AgentSDK, AuthenticatedActor
from support_agent.supply_http import HttpSupplyStore
from support_agent.handoff import HandoffQueue

source = HttpSupplyStore("https://your-erp-gateway.example", read_only_service_token)
handoffs = HandoffQueue(persistent_ticket_database_path)
sdk = AgentSDK.for_supply_chain(source, handoffs)

# Inside your already authenticated and authorized server request handler:
actor = AuthenticatedActor(request.session.user_id)
result = sdk.ask(request.json["question"], actor=actor)
return result.to_dict()
```

`request.session.user_id` above must come from your verified server-side session and staff authorization. Never construct `AuthenticatedActor` from a request body, a chat message, or a model output. The SDK checks that an actor is present for supply questions; your backend decides which staff may access which data and must enforce site or role restrictions. A guest supply question returns a sign-in clarification without reading the source.

For an environment-driven factory using the real HTTPS gateway or synced SQLite source, see [examples/sdk_integration.py](../examples/sdk_integration.py). It can enable Groq and OpenRouter with `SUPPLY_AGENT_MODE=multi` and both keys, and approved documents with HTTPS Qdrant plus Groq. The host may instead pass a `configured_chain(...)` provider chain to `AgentSDK.for_supply_chain(..., providers=chain)`. The same guarded tool and evidence checks apply.

## Company adapters

Implement `SupplySource` to connect your own read-only ERP/WMS/TMS service. It must provide `ready()`, `inventory(sku)`, `inventory_all()`, `purchase_order(id)`, and `shipment(id)`. Return the fields and timezone-aware `observed_at` timestamps in [REAL_DATA.md](REAL_DATA.md). `ready()` must reject missing or stale source syncs. Reads must fetch current records or raise on failure; do not quietly return cached data. The agent checks record freshness, allowed states, quantities, and overdue dates before answering. For complete low-stock answers, `inventory_all()` must provide the full current set, with at most 5,000 rows.

Implement `HandoffSink` to connect ServiceNow, Zendesk, Jira, or your internal workflow. `enqueue(reply, actor_id)` must durably create a review item and return its ticket ID; `ready()` reports whether it can accept tickets. The `Reply.handoff` payload contains the question and checked sources, so apply your retention and access rules. `HandoffQueue` is the bundled SQLite implementation for a protected backend.

For store support, use `AgentSDK.for_customer_support(source, status, handoffs)`. `SupportSource` exposes `ready()`, `order_for_customer(order_id, customer_id)`, and `catalog()`; `status` exposes `get(order_id)` and returns a `Status` with a timezone-aware observation. The existing `DynamicStoreData` and `HttpStatusProvider` are ready-made adapters. Pass the authenticated customer ID as `AuthenticatedActor` so order ownership is checked before status lookup.

Optional approved guidance can be supplied through `knowledge` and `document_answerer` arguments. The existing `KnowledgeBase` and `GroqDocumentAnswerer` use Qdrant retrieval and exact quote validation. Operational facts always come from the operational source.

## Response and failure behavior

`sdk.ask(...)` returns `SDKResponse` with `outcome` (`answered`, `clarify`, or `handoff`), `reason`, `message`, `sources`, `explanation`, and `ticket_id`. `handoff_status` is `not_required`, `queued`, or `unavailable`. The serialized result omits the internal handoff payload. Display `ticket_id` only when status is `queued`.

If a source is unavailable or raises, the SDK returns a handoff instead of an unverified answer. If ticket creation fails, it returns `handoff_status="unavailable"`, no ticket ID, and a message directing the user to contact the team. Surface that state prominently in your UI and alert operators. `sdk.ready()` checks source and ticket readiness for your application health endpoint.

The SDK is synchronous and safe to construct once per backend worker; adapters are called on each question. It provides no tenant isolation by itself. Use one SDK instance per tenant with separate credentials, source, Qdrant collection, and handoff destination. The host must enforce authentication, authorization, TLS, rate limits, request logging rules, and incident handling.
