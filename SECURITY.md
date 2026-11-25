# Security and privacy

## Trust boundaries

Customer questions, policy files, catalog files, order records, and status API responses are data. None can override the agent's routing or authorization rules. This implementation uses deterministic matching and never executes source text as instructions.

The HTTP API derives `customer_id` from an HMAC-SHA256 signed, expiring bearer token. A trusted storefront service must issue tokens after authenticating the customer. `customer_id` supplied in a question or JSON body is ignored. The local CLI's `--customer-id` is for testing only.

Order lookup checks ownership before requesting live status. Unknown and unauthorized order IDs receive the same response and reveal no order details. The agent has read-only behavior and cannot change orders, payments, or addresses.

## Deployment requirements

- Use TLS at the public gateway. Bind the reference server to localhost behind that gateway.
- Generate a random `SESSION_SECRET` of at least 32 characters and store it in a secret manager. Rotate it if exposed. The `token` CLI command is only for development or a protected backend.
- Keep `STATUS_API_TOKEN` in a secret manager. Allowlist the status API host, use HTTPS, and restrict the token to read-only status access.
- Authenticate the storefront user before issuing a token. Add gateway rate limits, request limits, monitoring, and a support queue with access controls.
- Store handoff payloads only in the approved support system, with retention and deletion rules. Customer questions can contain personal information.
- Never commit actual customer data. The `data/` directory contains fictional examples.

## Failure behavior

The agent hands off on stale, malformed, missing, or conflicting status data, and on upstream failure. It does not expose raw upstream error messages or credentials to customers. Handoff payloads contain the customer question and order number, so protect them as customer data.

## Reporting a vulnerability

Please report security issues privately to the repository owner through GitHub's private vulnerability reporting feature, if enabled. Avoid posting customer data or exploit details in public issues.
