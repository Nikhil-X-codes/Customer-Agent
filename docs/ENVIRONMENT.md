# Configure `.env` for a real deployment

Copy `.env.example` to `.env`. The loader reads this file from the working directory. The file is ignored by Git; keep actual credentials in a company secret manager for deployment. Never put a real key in an `*.example` file.

## Required choices

| Variable | Value to enter | Required when |
| --- | --- | --- |
| `APP_MODE` | `production` | Always |
| `AGENT_DOMAIN` | `supply_chain` | Supply agent |
| `SUPPLY_API_URL` | Base HTTPS URL of the company's read-only supply gateway | API source; leave empty for database source |
| `SUPPLY_API_TOKEN` | Read-only service token issued for that gateway | API source |
| `SUPPLY_DB_PATH` | Absolute path to the current, complete ERP/WMS/TMS SQLite sync | Database source; leave `SUPPLY_API_URL` empty |
| `SUPPLY_MAX_SYNC_AGE_MINUTES` | `30` initially; tune to actual sync cadence (1–1440) | Always |
| `HANDOFF_DB_PATH` | Absolute, persistent, writable path such as `/srv/customer-agent/handoffs.db` | Always |
| `AUTH_MODE` | `oidc` for company RS256 access tokens, or `signed` for a trusted backend issuer | HTTP server; defaults to `signed` |
| `SESSION_SECRET` | Random string of at least 32 characters, generated locally | `AUTH_MODE=signed` and local MCP adapter |
| `OIDC_ISSUER` | Exact issuer (`iss`) HTTPS URL in company access tokens | `AUTH_MODE=oidc` |
| `OIDC_AUDIENCE` | Exact audience (`aud`) assigned to this service | `AUTH_MODE=oidc` |
| `OIDC_JWKS_URL` | HTTPS JWKS URL published by the company identity provider | `AUTH_MODE=oidc` |
| `OIDC_ROLE_CLAIM` | Name of the array claim containing staff roles, usually `roles` | `AUTH_MODE=oidc`; defaults to `roles` |
| `OIDC_REQUIRED_ROLE` | The operations role value in that claim | Supply agent with `AUTH_MODE=oidc` |

Set **exactly one** source: API URL or database path. Both must contain real operational data matching [the source contract](REAL_DATA.md). The application verifies freshness before answering. The company gateway must still enforce site and tenant authorization; a valid operations role alone does not grant access to every site. Use a separate deployment per tenant unless your host supplies complete tenant isolation.

## AI and approved documents

| Variable | Value to enter | Required when |
| --- | --- | --- |
| `AI_MODE` | `off` for deterministic routing; `groq` for tool-calling agents or document answers | Always |
| `SUPPLY_AGENT_MODE` | `deterministic` or `multi` | Always; `multi` requires `AI_MODE=groq` |
| `GROQ_API_KEY` | Your Groq API key, entered only in private `.env` or a secret manager | `AI_MODE=groq` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Optional model override |
| `OPENROUTER_API_KEY` | Your OpenRouter API key | `SUPPLY_AGENT_MODE=multi`; fallback for transient provider failures |
| `OPENROUTER_MODEL` | `openai/gpt-oss-120b` | Optional fallback model override |
| `QDRANT_URL` | HTTPS base URL of your private Qdrant service | Approved document answers |
| `QDRANT_API_KEY` | Qdrant service key, if required by your instance | Approved document answers on a protected Qdrant service |
| `QDRANT_COLLECTION` | `supply_chain_live_knowledge_v1` | Optional collection override; must begin `supply_chain_live_` |
| `EMBEDDING_CACHE_PATH` | Writable absolute directory for the FastEmbed model cache | Document answers |
| `ADMIN_API_KEY` | Separate random string of at least 32 characters | Serving with `QDRANT_URL`; protects document upload endpoints |

`AI_MODE=off` and `SUPPLY_AGENT_MODE=deterministic` work without AI keys. To test the requested multi-agent chain, set `AI_MODE=groq`, `SUPPLY_AGENT_MODE=multi`, and fill both provider keys. Operational answers still depend on the live source. Qdrant stores approved guidance only; it cannot replace the current inventory, purchase order, or shipment source.

## Example for the bundled Docker deployment

Start from `.env.example` and enter your own values in the **private** `.env`. For a company HTTPS supply gateway and OIDC, these are the relevant lines:

```dotenv
APP_MODE=production
AGENT_DOMAIN=supply_chain
SUPPLY_API_URL=https://supply-api.company.example
SUPPLY_API_TOKEN=YOUR_READ_ONLY_SUPPLY_TOKEN
SUPPLY_MAX_SYNC_AGE_MINUTES=30
AUTH_MODE=oidc
OIDC_ISSUER=https://id.company.example
OIDC_AUDIENCE=customer-agent
OIDC_JWKS_URL=https://id.company.example/.well-known/jwks.json
OIDC_ROLE_CLAIM=roles
OIDC_REQUIRED_ROLE=operations
HANDOFF_DB_PATH=/app/var/handoffs.db
AI_MODE=groq
SUPPLY_AGENT_MODE=multi
GROQ_API_KEY=YOUR_GROQ_KEY
GROQ_MODEL=openai/gpt-oss-120b
OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY
OPENROUTER_MODEL=openai/gpt-oss-120b
```

The `YOUR_...` values and example URLs are placeholders. Replace them with live company values. The reference Compose file overrides `HANDOFF_DB_PATH` and `EMBEDDING_CACHE_PATH` to persistent container paths. If you use `SUPPLY_DB_PATH` instead of `SUPPLY_API_URL`, add a **read-only bind mount** for the database at that exact container path. For local Python runs, choose absolute paths on your host instead of `/app/var`.

To use company documents too, add `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `EMBEDDING_CACHE_PATH`, and a distinct `ADMIN_API_KEY`. Upload only approved documents through the protected admin endpoint. Generate random values with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

Run `python -m support_agent.cli doctor` after configuration. It checks source and storage readiness but does **not** call Groq or OpenRouter. Then test the actual provider chain, role boundaries, stale records, source outages, and human ticket workflow in staging before serving users. See [production launch steps](PRODUCTION.md).
