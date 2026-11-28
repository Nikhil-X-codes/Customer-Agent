# Delivery phases

## Production integration boundary (implemented); company integration pending

- [x] Add OIDC RS256 access-token verification with issuer, audience, and staff role checks.
- [x] Bound HTTP workers and requests, return request IDs, and log decisions without customer text.
- [x] Fail closed on unexpected agent and handoff failures.
- [x] Provide a reference hardened deployment and readiness checks.
- [ ] Connect a company identity provider, source, ticket workflow, and monitoring.

## Embeddable SDK (implemented); company integration pending

- [x] Expose a typed Python entry point for supply chain and customer support.
- [x] Accept company read-only source and human ticket adapters.
- [x] Return source-backed explanations and explicit ticket delivery status.
- [x] Fail closed on source outages, bad adapter data, and ticket sink errors.
- [ ] Connect a company SSO session, authorized source, and ticket workflow.
- [ ] Run held-out evaluation and end-to-end staging checks with company data.

## Supply chain extension (runtime source enforcement complete); company integrations pending

- [x] Add a separate supply chain mode for inventory, purchase orders, and shipments.
- [x] Require verified operations identity, read current tables, and check freshness and valid states.
- [x] Hand off changes, overdue records, stale data, and uncertainty; persist tickets.
- [x] Adapt the browser UI and MCP tool to the selected domain.
- [x] Isolate uploaded supply guidance in its own Qdrant collection and add labeled local evaluation.
- [ ] Connect actual ERP/WMS/TMS feeds, staff identity, approved documents, and an operations team.
- [ ] Measure accuracy and handoff quality on redacted real supply chain questions.

## Multi-agent tool calling (simulated-provider tested); live provider validation pending

- [x] Add a supervisor and domain specialists with one allowlisted read-only tool call per question.
- [x] Validate route confidence, tool arguments, exact record IDs, freshness, and evidence before reply.
- [x] Add Groq primary and OpenRouter transient-failure fallback.
- [x] Test function calls, fallback, invalid decisions, and human handoff with simulated providers.
- [ ] Test Groq and OpenRouter with real keys and approved staging data.
- [ ] Evaluate routing and tool accuracy on held-out real operations questions.

## Real-data guardrails (implemented); source integration pending

- [x] Require explicit serving mode and production-only startup.
- [x] Reject demo supply databases and stale source metadata in production.
- [x] Add a read-only HTTPS supply gateway contract with freshness and response checks.
- [x] Use live document collections only.
- [ ] Connect an actual ERP/WMS/TMS feed and staff identity service.
- [ ] Validate the external schema, authorization, freshness, and human ticket flow in staging.

## Phase 1 — Safe baseline (complete)

- [x] Ground product and policy answers in curated records.
- [x] Check customer identity and order ownership before status lookup.
- [x] Stop and hand off high-stakes or uncertain requests.
- [x] Add CLI, HTTP interface, tests, and a labeled evaluation set.
- [x] Add a responsive browser chat UI with progress and streamed verified replies.

## Phase 2 — Integration-ready local flow (complete); real store connection pending

- [x] Add a read-only SQLite order table adapter.
- [x] Add a read-only dynamic catalog adapter; policy and product updates appear on the next request.
- [x] Add an HTTPS live status adapter with timeout, freshness, identity, and response limits.
- [x] Persist tickets to a local SQLite support queue; support list and close operations.
- [x] Require real data configuration in production mode and reject demo fallback.
- [ ] Provide an approved catalog sync, real order database, and live status endpoint.
- [ ] Connect the store's actual sign-in system to token issuance and its human team to the queue.

## Phase 2b — Uploaded knowledge (local flow complete); real content pending

- [x] Add administrator-authenticated document upload, list, replace, and delete.
- [x] Index text, Markdown, and text-based PDF passages as FastEmbed vectors in Qdrant.
- [x] Route order and high-stakes requests before document retrieval.
- [x] Use Groq structured output to select exact evidence quotes; validate them and hand off on uncertainty.
- [x] Show document evidence in the chat UI and test the upload-to-answer path.
- [ ] Upload approved company documents, review stale/conflicting content, and measure answers on held-out real questions.

## Phase 3 — Evaluation and resilience (local tooling complete); production validation pending

- [x] Expose the support decision flow as a local stdio MCP tool with signed order identity and persistent handoff behavior.
- [x] Test MCP tool discovery and calls through the SDK protocol.

- [x] Score labeled cases for case accuracy and handoff precision and recall.
- [x] Test authorization, conflicting and stale status, upstream failure, prompt injection, and an HTTP ticket flow.
- [x] Prevent untrusted carrier detail from becoming an answer.
- [x] Add optional AI routing for paraphrases while retaining deterministic authorization and source-backed answers.
- [x] Test simulated model responses, invalid source IDs, low confidence, and AI outages.
- [x] Add Groq structured-output routing for `openai/gpt-oss-120b` and local `.env` loading.
- [x] Add a large-catalog candidate shortlist and a configuration doctor command.
- [ ] Supply a held-out set of redacted real customer questions and review incorrect confident answers.
- [ ] Measure live model routing accuracy and calibrate the confidence threshold on that set.
- [ ] Agree on acceptance thresholds for real data, languages, and traffic patterns.
- [ ] Run end-to-end tests against staging store services.

## Phase 4 — Deployment preparation (complete); launch pending

- [x] Provide a non-root Docker image, sample configuration, security guidance, and an operator runbook.
- [x] Configure the wheel and container for production startup.
- [x] Require explicit production configuration and durable handoff storage.
- [ ] Deploy behind TLS, storefront authentication, rate limits, monitoring, and backup.
- [ ] Set support owners, response time targets, data retention, and incident procedures.
- [ ] Gradually enable production traffic and monitor wrong answers and handoffs.

The remaining items require real store systems, approved content, a human support owner, and a deployment environment. Production readiness cannot be claimed until those checks pass.
