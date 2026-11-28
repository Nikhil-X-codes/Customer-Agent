# Requirements and evidence

Supply chain mode adds read-only inventory, purchase order, and shipment answers, with signed staff identity and human handoff for actions, stale records, and overdue dates. Its fictional evaluation set is separate from the original customer support brief. See `docs/SUPPLY_CHAIN.md` for schema and production inputs.

Multi-agent mode adds a supervisor, inventory/procurement/logistics/guidance specialists, validated function calls, and a Groq-to-OpenRouter transient-failure chain. Local tests simulate both providers; live model accuracy remains unmeasured. See `docs/MULTI_AGENT.md`.

| Brief requirement | Implementation | Verification | Status |
| --- | --- | --- | --- |
| Answer product questions | Current SQLite product table or uploaded approved documents retrieved from Qdrant vectors, with Groq evidence checking | Dynamic-update, upload-to-answer, labeled, and AI tests | Local flow complete; real content and live model evaluation pending |
| Answer policy questions | Current SQLite policy table or uploaded documents; source ID and quote included | Dynamic catalog, document replacement, and evidence tests | Local flow complete; approved store content pending |
| Read order table | Read-only SQLite adapter checks verified customer ID | Cross-customer and database-failure tests | Adapter complete; real table needed |
| Read real-time status | HTTPS adapter checks order ID, timestamp, state, size, and errors | Adapter and stale/conflict tests | Adapter complete; endpoint needed |
| Know when to stop | Hard rules, AI routing, confidence threshold, unknown source check | High-stakes, outage, invalid source, and ambiguity tests | Synthetic tests pass; threshold needs real-data calibration |
| Human handoff | Durable SQLite queue with ticket ID, verified customer ID, question, reason, and sources | HTTP queue test and operator commands | Local queue complete; support-platform integration needed |
| Customer interaction | Responsive browser chat, source labels, signed-session entry, and Server-Sent Event replies | UI asset and streaming HTTP test | Local UI complete; storefront session integration pending |
| MCP access | Local stdio `ask_support` tool delegates to the same guarded agent and queue | MCP protocol and signed-order tests | Local host integration complete; production host configuration pending |
| One failure handled well | Conflicting `ORD-1003` data creates a ticket without a delivery promise | Synthetic status-conflict integration test | Covered by synthetic test |
| Measured accuracy | Labeled evaluator reports accuracy and handoff precision/recall | synthetic cases | Local metric only; hidden and real-data results unknown |
| Security | Signed sessions, ownership check, bounded HTTP input, read-only behavior | Authorization and token tests | Local controls complete; gateway and data governance needed |
| Deployment | Installable wheel, non-root Docker image, health and readiness endpoints, CI, runbook | Installed-package and container startup check | Package complete; launch needs environment |

## Inputs needed for a real-store launch

1. Approved policy and product records or uploaded documents, a content owner, and a process to replace stale versions.
2. Order table schema or read-only API and a mapping from authenticated storefront users to customer IDs.
3. Live status API contract, state vocabulary, read-only credentials, and expected freshness.
4. Human support platform or queue destination, support owner, and response-time target.
5. Redacted held-out questions and agreed safety and accuracy thresholds.
6. Deployment target, TLS gateway, secret manager, rate limits, monitoring, backup, and retention rules.

The repository does not contain those external systems or credentials. Runtime requires production configuration and never packages test records. Launch readiness requires real integrations and held-out evaluation.
