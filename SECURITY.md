# Security and privacy

## Trust boundaries

Customer questions, policy files, catalog files, order records, and status API responses are data. None can override the agent's routing or authorization rules. The offline matcher is deterministic. Optional AI routing returns a label and source ID that application code validates; it never executes source text as instructions.

Supply inventory, purchase order, and shipment rows are also data. The supply agent reads them locally with parameterized, read-only SQL and checks timestamp freshness, nonnegative quantities, and allowed states before answering. In multi-agent mode, selected tool results can be sent to Groq or OpenRouter for routing and synthesis. Approve both providers for the classification of those records.

Multi-agent supply mode sends the question and read-only tool schemas to Groq, with OpenRouter as a fallback for transient provider failures. Guidance questions also send retrieved approved passages. Both providers must be approved for the intended data classification. The application rejects invented tool IDs, unexpected tool calls, low confidence, and invalid evidence; it does not fall back to another model after a safety failure. Keep both API keys in a secret manager or an uncommitted owner-readable `.env`.

Uploaded documents are administrator-approved content, but can still contain misleading instructions or stale claims. General-question retrieval uses Qdrant semantic search; Groq selects exact source quotes, and the server checks the quotes before returning them. High-stakes and personal order routing runs first. Document answer failures create a handoff. Replacing a document writes new vectors before removing old ones; an interrupted replacement can temporarily leave both versions and requires an administrator retry. Scanned PDFs require external OCR; unsupported files are rejected.

The HTTP API can derive identity from either an HMAC-SHA256 signed, expiring bearer token issued by a trusted company backend or a validated OIDC RS256 access token. OIDC checks issuer, audience, expiry, signature, and a required staff role in supply mode. `customer_id` supplied in a question or JSON body is ignored. The CLI does not issue session tokens.

In supply chain mode, every operational question requires an authorized staff actor. OIDC mode checks a required staff role; signed-token mode depends on the trusted issuer to authorize staff before token issuance. The agent does not implement an employee directory or site-level permissions; the company gateway must enforce those restrictions. Supply records and guidance can contain confidential commercial data. Restrict the web app, MCP host, Qdrant collection, and handoff queue accordingly.

Production supply mode requires `APP_MODE=production`, exactly one real source, and a fresh source marker. The runtime contains no demo data, seed command, or sample-token endpoint. These checks reduce accidental fixture exposure; they do not prove that a configured external system contains genuine company records.

The browser UI keeps its session token in memory and does not save it to browser storage. Static assets have a restrictive Content Security Policy. A trusted storefront or staff identity service must provide tokens.

The optional MCP server uses local stdio and shares the web agent's decision rules. It accepts a signed `session_token` for customer order status and never accepts a caller-supplied customer ID. It does not issue tokens or expose raw order and document records. Run it only under a trusted MCP host, protect the `.env` path, and do not forward its stdio transport to an untrusted network service.

Order lookup checks ownership before requesting live status. Unknown and unauthorized order IDs receive the same response and reveal no order details. The agent has read-only behavior and cannot change orders, payments, or addresses. It never echoes arbitrary carrier detail text.

With `AI_MODE=groq` or `AI_MODE=openai`, the app sends the customer question and approved policy/product records to the chosen model provider for routing. It replaces order numbers and email addresses and never sends customer IDs, order table rows, or live status data. Free text can still contain personal information. Review provider terms, retention settings, and customer consent requirements before enabling AI for real traffic. For document questions, Groq receives up to six retrieved passages of approved company text and a question with order numbers and email addresses redacted. Other personal details in free text may remain. The model returns selected quotes; application code checks the quotes and returns only verified source text. For catalog routing, the model returns a label and source ID; application code validates the ID and produces the answer.

## Deployment requirements

- Use TLS at the public gateway. Bind the reference server to localhost behind that gateway.
- For `AUTH_MODE=signed`, generate a random `SESSION_SECRET` of at least 32 characters and store it in a secret manager. Rotate it if exposed. Issue tokens only from a trusted backend after authentication and authorization. For `AUTH_MODE=oidc`, configure an HTTPS JWKS URL, expected issuer/audience, and supply staff role; rotate keys through the identity provider.
- Keep `STATUS_API_TOKEN` in a secret manager. Allowlist the status API host, use HTTPS, and restrict the token to read-only status access.
- Authenticate the storefront user before issuing a token. Add gateway rate limits, request limits, monitoring, and a support queue with access controls.
- Store handoff payloads only in an approved support system, with retention and deletion rules. The local SQLite queue is a reference implementation and requires protected shell access, backup, and a connection to the store's support process before launch. Customer questions can contain personal information.
- Never commit actual customer data. Synthetic fixtures exist only under `tests/` and are not packaged.
- Keep `GROQ_API_KEY` or `OPENAI_API_KEY` in a secret manager for production. A local `.env` file must stay uncommitted and should be readable only by its owner. Restrict outbound access to the chosen model API. AI errors, invalid responses, or low-confidence decisions must hand off.
- Protect `/admin` and `/admin/documents` behind a TLS gateway and administrator access controls. Use a long random `ADMIN_API_KEY`; the UI holds it only in browser memory. Restrict access to and back up the Qdrant server storage or hosted collection, which contains extracted company text. Review uploaded content for secrets and customer data before ingestion.

## Failure behavior

The agent hands off on stale, malformed, missing, or conflicting status data, and on upstream failure. It does not expose raw upstream error messages or credentials to customers. Handoff payloads contain the customer question and order number, so protect them as customer data.

## Reporting a vulnerability

Please report security issues privately to the repository owner through GitHub's private vulnerability reporting feature, if enabled. Avoid posting customer data or exploit details in public issues.
