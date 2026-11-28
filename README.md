# Customer Agent: supply chain and store support

A read-only agent for current supply records and approved company documents. It answers routine inventory, purchase-order, and shipment questions, then hands actions, overdue records, stale data, and uncertain questions to a person. `AGENT_DOMAIN=support` provides the original customer support workflow for a real order/catalog database and live status API.

**Embed it in your product:** install the Python package and use `AgentSDK.for_supply_chain(...)` or `AgentSDK.for_customer_support(...)` with your own source, identity, and handoff integrations. See the [SDK guide](docs/SDK.md) and [real-source factory example](examples/sdk_integration.py). The bundled server and UI are optional.

For company launch, follow the [production integration guide](docs/PRODUCTION.md). The web workspace uses locally compiled Tailwind CSS. Edit [assets/tailwind.css](assets/tailwind.css) and the page templates, then run `npm ci` and `npm run build:css`. The generated `support_agent/web/app.css` is included in the Python package and served without a CDN or Node process. `npm run watch:css` rebuilds during UI work.

**This repository contains no company data.** Runtime startup requires your own operational source. Synthetic records are confined to `tests/` and are never packaged with the app. No model keys or live ERP connection are present, so results with your providers and data remain unmeasured.

## Configure a real supply source

1. Copy [.env.example](.env.example) to `.env` and follow the [environment value guide](docs/ENVIRONMENT.md). Set an absolute `HANDOFF_DB_PATH`, then choose either company OIDC or a trusted signed-token issuer with `SESSION_SECRET`.
2. Set exactly one source: `SUPPLY_API_URL` plus its read-only `SUPPLY_API_TOKEN`, or an absolute `SUPPLY_DB_PATH` maintained by your ERP/WMS/TMS sync. The required endpoints and database tables are in [docs/REAL_DATA.md](docs/REAL_DATA.md).
3. Run `python -m pip install -e '.[knowledge,mcp,oidc]'`, then `python -m support_agent.cli doctor`. The source must be current and valid before the app will start.
4. Run `python -m support_agent.cli serve --host 127.0.0.1 --port 8000`. Open the browser UI and provide a company access token. With `AUTH_MODE=oidc`, the server verifies issuer, audience, signature, expiry, and the configured staff role. The company still owns SSO, site-level authorization, and token issuance.

The supply source is read on each question. A source outage, stale sync, stale record, unsupported state, or action request yields a human handoff. The local SQLite ticket queue needs an operations team workflow before serving users. `/ready` checks source, handoff storage, and optional Qdrant health.

## AI, documents, and explanations

For tool-calling multi-agent mode, set `AI_MODE=groq`, `SUPPLY_AGENT_MODE=multi`, `GROQ_API_KEY`, and `OPENROUTER_API_KEY`. The Groq model defaults to `openai/gpt-oss-120b`; OpenRouter is a fallback for transient provider failures. The model selects read-only tools; application code validates tool arguments, source freshness, and handoff decisions. The keys are not needed for deterministic mode.

For approved company guidance, configure an HTTPS `QDRANT_URL`, `QDRANT_API_KEY`, and `ADMIN_API_KEY`. Upload text, Markdown, or text-based PDF files at `/admin`. The app uses semantic vector retrieval plus exact quote verification for document answers, not an unverified text chunk. Operational facts still come from the current source. Groq is required for production document answering. See [docs/SUPPLY_CHAIN.md](docs/SUPPLY_CHAIN.md).

Every JSON response and streaming `meta` event includes `explanation`: outcome, reason code, source IDs, checks performed, and validated agent steps. The UI displays this under **Why this response**. These summaries are generated from the final application decision, not hidden model reasoning. No explanation claims a check when the agent handed off before that check.

`POST /chat` accepts `{"question":"Stock for SKU-123?"}` and an `Authorization: Bearer <company-token>` header. `POST /chat/stream` emits progress, metadata, chunks, and completion. Streaming delivers the final checked answer in chunks; it is not raw model token output. `GET /ui/config` exposes UI capabilities, and `/admin/documents` requires `X-Admin-Key`. The MCP stdio server exposes `ask_supply_chain` for a trusted host; see [examples/mcp-config.example.json](examples/mcp-config.example.json).

## Customer support mode

Set `AGENT_DOMAIN=support`, `APP_MODE=production`, `ORDER_DB_PATH`, `STATUS_API_URL`, `STATUS_API_TOKEN`, and either `CATALOG_DB_PATH` or Qdrant with Groq. Orders and catalog tables are read from the supplied SQLite databases; statuses come from the live HTTPS API. See [docs/REAL_DATA.md](docs/REAL_DATA.md) and [SECURITY.md](SECURITY.md). The customer UI requires a token from the configured signed-token issuer or OIDC provider.

## Verification

Run `python -m unittest discover -s tests -q`. Tests use synthetic data under `tests/` and simulated provider responses. For a redacted set of your own labeled questions, run `python -m support_agent.cli eval --cases /absolute/path/to/cases.json`. This reports case accuracy and handoff precision/recall. Do not infer production accuracy from the test fixtures. Provider keys and actual source integration need to be tested in your environment.

See the [architecture overview](docs/ARCHITECTURE.md), [AGENT.md](AGENT.md) for implementation rules, [SECURITY.md](SECURITY.md) for deployment controls, and [docs/OPERATIONS.md](docs/OPERATIONS.md) for operations.
