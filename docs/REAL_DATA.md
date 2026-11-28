# Real supply data contract

Production serving requires `AGENT_DOMAIN=supply_chain`, `APP_MODE=production`, exactly one real source, a trusted identity configuration, and an absolute persistent `HANDOFF_DB_PATH`. A missing, stale, or invalid source stops startup or hands the question to a person. The runtime has no demo data or sample-token endpoint. Synthetic records are confined to automated tests.

The Docker image defaults to production supply mode and refuses to start until secrets and a real source are configured. `eval` requires an explicit `--cases` file.

This repository cannot connect to a company system until its actual URL/schema, authentication method, and staff identity integration are supplied. The generic contracts below are implemented and tested with simulated responses; they have not been validated against an ERP/WMS/TMS provider.

## Option A: HTTPS gateway

Set `SUPPLY_API_URL=https://...` and `SUPPLY_API_TOKEN` to a read-only service token. Leave `SUPPLY_DB_PATH` unset. The adapter rejects non-HTTPS URLs, redirects, oversized or malformed JSON, incomplete inventory lists, identifier mismatches, and upstream errors. It calls `/health` before every operational read; a stopped sync fails closed. The gateway must implement:

| Endpoint | Response |
| --- | --- |
| `GET /health` | `{"status":"ready","source":"Acme ERP","synced_at":"2026-11-27T12:00:00Z"}` |
| `GET /inventory/{sku}` | One inventory object or HTTP 404 |
| `GET /inventory?limit=5001` | `{"items":[inventory objects],"complete":true}`; no hidden pagination |
| `GET /purchase-orders/{id}` | One purchase order object or HTTP 404 |
| `GET /shipments/{id}` | One shipment object or HTTP 404 |

Inventory objects require `sku`, `product_name`, `location`, integer `on_hand`, integer `reserved`, integer `reorder_point`, and timezone-aware `observed_at`. Purchase orders require `id`, `supplier`, `sku`, integer `quantity`, `state`, `expected_at`, and `observed_at`. Shipments require `id`, `purchase_order_id`, `carrier`, `state`, `eta`, and `observed_at`. The response identifiers must equal the requested identifiers. The agent checks allowed states, quantities, source sync age, record freshness, and overdue dates. A missing record yields a clarification; bad data yields a handoff.

The API token never goes to Groq or OpenRouter. Restrict it to read-only endpoints, and place the gateway on a private network with TLS and request logging that excludes tokens.

## Option B: synced database

Set an absolute `SUPPLY_DB_PATH=/secure/mounted/erp-sync.db` and leave `SUPPLY_API_URL` unset. The schema for `inventory`, `purchase_orders`, and `shipments` is in `docs/SUPPLY_CHAIN.md`. A real sync job must also maintain:

```sql
CREATE TABLE source_metadata (
  kind TEXT NOT NULL,
  source_system TEXT NOT NULL,
  synced_at TEXT NOT NULL
);
```

Keep one row with `kind='live'`, a non-demo `source_system`, and a timezone-aware `synced_at`. Update that row only after a successful, complete sync. The production adapter reads the database in read-only mode and checks this marker before every query. The default maximum sync age is 30 minutes (`SUPPLY_MAX_SYNC_AGE_MINUTES`, allowed 1–1440). Inventory and purchase order observations may be up to 24 hours old; shipment observations may be up to two hours old. Align these limits with the actual source cadence before launch.

The marker is an operational safeguard, not proof of data authenticity. Protect the sync job, file, and mount permissions. `doctor` checks the configuration and source readiness but does not validate the entire dataset or perform a live Groq/OpenRouter call.

## Staff access and launch gate

The reference HTTP app accepts either trusted signed tokens or validated OIDC access tokens. OIDC supply mode requires a configured staff role. It does not include an employee directory or site-level authorization; the company gateway must enforce those controls. The reverse proxy must restrict access to the UI and admin endpoints. Connect handoff tickets to an operations team with notifications, ownership, and retention. Upload approved production documents into the live Qdrant collection.

For signed-token mode, the trusted issuer can call `support_agent.auth.issue_token(staff_id, SESSION_SECRET, ttl_seconds)` after its own SSO and role check. OIDC mode validates an RS256 access token against the configured HTTPS JWKS, issuer, audience, and staff role. The API ignores any staff ID sent in the request body. The local CLI never issues tokens.

Before real users, test with a staging copy of actual records, two staff roles/sites, a stopped sync, stale shipments, API timeouts, unauthorized access, Groq failure and OpenRouter fallback, and a human ticket from creation to resolution. Evaluate on redacted real questions. Do not treat synthetic unit tests as production accuracy.
