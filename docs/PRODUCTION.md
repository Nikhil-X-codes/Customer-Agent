# Production integration and launch

This repository supplies guarded agent logic, an HTTP application, and an embeddable Python SDK. It does **not** contain a company's data, identity service, documents, ticket workflow, or provider credentials. A deployment is ready for users only after the company connects and validates those systems.

## Integration choice

- **Python backend:** Embed `AgentSDK` from `docs/SDK.md`. Pass an authenticated actor from your server session, a current read-only source adapter, and a durable ticket sink. This avoids a second HTTP service.
- **HTTP application:** Use the bundled server behind the company's TLS gateway. `AUTH_MODE=oidc` verifies RS256 access tokens against the company's HTTPS JWKS, issuer, and audience. Supply mode requires `OIDC_REQUIRED_ROLE` in the configured list claim. `AUTH_MODE=signed` accepts the repository's HMAC tokens issued by a trusted company backend; use a unique secret per deployment. The local MCP stdio adapter still uses signed tokens and should run only under a trusted host.

Deploy one instance per tenant unless the host provides separate source credentials, document collections, token audiences, and ticket sinks for each tenant. The reference HTTP server is single-process; its request limits are per process. The gateway must also enforce authentication, authorization, rate limits, TLS, and network policy.

## Source and answer path

Configure exactly one live supply source: `SUPPLY_API_URL` with a read-only token, or an absolute `SUPPLY_DB_PATH` maintained by a complete ERP/WMS/TMS sync. The source contract and freshness limits are in `docs/REAL_DATA.md`. The agent does not write to operational systems. For guidance answers, use an approved HTTPS Qdrant collection; a model-selected quote is checked against retrieved text. Groq is primary for optional multi-agent routing, with OpenRouter fallback only for transient provider failure. Evaluate both providers on redacted real questions before enabling them.

## Human review and observability

The bundled SQLite handoff database needs a protected persistent volume, backup, retention, and a team that actually monitors tickets. For a company ticket platform, implement the SDK's `HandoffSink`. A handoff queue failure must surface to the user as unavailable and alert operators; it must never be presented as a completed ticket.

`GET /health` reports process liveness. `GET /ready` checks handoff storage, the live source, and optional Qdrant. HTTP responses include `X-Request-ID`. Decision logs contain request ID, domain, outcome, reason, and elapsed time; they intentionally exclude questions, tokens, actor IDs, and source records. Route logs and readiness failures to the company's monitoring system. The app limits concurrent HTTP workers and chat requests per client, but the gateway needs distributed rate limiting for multiple replicas.

## Deployment steps

1. Copy `.env.example` to a protected `.env` and follow [the environment value guide](ENVIRONMENT.md). Configure a real source, absolute handoff path, and either a trusted signed-token issuer or OIDC settings. For OIDC, install `customer-agent[oidc]`; the Docker image already includes it.
2. Configure the reverse proxy or API gateway with TLS, staff access controls, and a private network route to the app. Do not expose the reference admin page directly to the internet. Use a company secret manager for keys.
3. Run `python -m support_agent.cli doctor`. It checks configuration, source readiness, handoff storage, and optional Qdrant; it does not make a model call. The check may create the handoff database. The reference `deploy/compose.production.yaml` binds the app only to localhost and mounts persistent state. For a synced SQLite source, add a read-only bind mount at the configured absolute container path.
4. Test real records, authorized and unauthorized users, two roles/sites if applicable, stale syncs, source outages, malformed records, overdue records, model fallback, and ticket creation through resolution. Run `eval --cases` with redacted held-out questions and agree on answer and handoff thresholds with source owners.
5. Start with limited traffic. Monitor incorrect answers, handoffs, source freshness, provider errors, ticket backlog, latency, and rate-limit responses. Keep a kill switch: set `SUPPLY_AGENT_MODE=deterministic` to disable model routing or stop traffic at the gateway.

Do not interpret passing synthetic unit tests as production accuracy or authorization validation against a real identity provider.
