# Supply chain mode

Set `AGENT_DOMAIN=supply_chain`. This mode is for an authenticated operations user, not public customers. The customer support flow remains available with `AGENT_DOMAIN=support`.

`SUPPLY_AGENT_MODE=deterministic` is the default. `SUPPLY_AGENT_MODE=multi` adds Groq and OpenRouter supervisor/specialist tool calling while keeping the same read-only data checks. See `docs/MULTI_AGENT.md`.

## What it answers

- Inventory by SKU: on hand, reserved, available (`on_hand - reserved`), location, and whether available stock is at or below its reorder point.
- A low-stock list, but only when every row in the current inventory snapshot passes validation. Large or incomplete lists go to a person.
- Purchase order state, supplier, SKU, quantity, and recorded expected date.
- Shipment state, carrier, linked purchase order, and recorded ETA. An ETA is labeled as an estimate.
- General procurement or logistics guidance from approved uploaded documents when Qdrant and Groq are configured.

The agent is read-only. It sends stock adjustments, new or changed purchase orders, expedited shipments, payment disputes, overdue records, and unverifiable information to a human queue. It does not forecast demand or recommend an autonomous action.

## Data source

Production uses either `SUPPLY_API_URL` for a real HTTPS gateway or `SUPPLY_DB_PATH` for a SQLite database synced from the organization's ERP, WMS, or TMS. The database adapter opens it read-only on every question and requires fresh `source_metadata`. No company records are included. See `docs/REAL_DATA.md` for the complete source contract:

```sql
CREATE TABLE inventory (
  sku TEXT PRIMARY KEY, product_name TEXT NOT NULL, location TEXT NOT NULL,
  on_hand INTEGER NOT NULL, reserved INTEGER NOT NULL,
  reorder_point INTEGER NOT NULL, observed_at TEXT NOT NULL
);
CREATE TABLE purchase_orders (
  id TEXT PRIMARY KEY, supplier TEXT NOT NULL, sku TEXT NOT NULL,
  quantity INTEGER NOT NULL, state TEXT NOT NULL,
  expected_at TEXT, observed_at TEXT NOT NULL
);
CREATE TABLE shipments (
  id TEXT PRIMARY KEY, purchase_order_id TEXT NOT NULL,
  carrier TEXT NOT NULL, state TEXT NOT NULL,
  eta TEXT, observed_at TEXT NOT NULL
);
```

Timestamps must include a timezone. Inventory and purchase order observations must be no older than 24 hours; shipment observations must be no older than two hours. Invalid quantities, unknown states, stale records, and overdue shipments or purchase orders go to a human. These thresholds are local defaults and should be calibrated to each source's actual update frequency before production.

## Identity and interfaces

A trusted internal identity service must provide signed tokens or OIDC access tokens for authorized operations users. The browser accepts a token, and the MCP server exposes `ask_supply_chain(question, session_token)`. Guest requests receive a sign-in clarification. For real users, protect the web app and MCP host with staff access controls. The handoff queue records the signed actor ID, question, reason, and checked source IDs.

Uploaded documents use `supply_chain_live_knowledge_v1`, separate from customer support collections. Groq selects exact evidence quotes for general guidance, while operational facts come from current supply sources. Set an HTTPS `QDRANT_URL`, `AI_MODE=groq`, and `GROQ_API_KEY` for document answering.

## Local checks and remaining work

Run `python -m unittest discover -s tests -q` for isolated checks. With a real source, run `python -m support_agent.cli eval --cases PATH` against approved redacted cases. Synthetic test scores do not predict real-world accuracy.

Before launch, provide a real ERP/WMS/TMS sync or adapter, authorized staff identity, approved guidance documents, an operations ticket owner, redacted held-out questions, and staging tests for stale data, outages, and delayed shipments. Confirm the allowed state vocabulary and freshness thresholds with the source owners.
