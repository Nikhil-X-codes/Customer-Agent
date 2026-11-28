# Operations

Copy `.env.example` to `.env`, connect a real read-only source, and run `python -m support_agent.cli doctor`. Start with `AI_MODE=off` and `SUPPLY_AGENT_MODE=deterministic`. After supplying both keys, enable Groq plus OpenRouter multi-agent mode and test with redacted real questions. `doctor` checks configuration and source readiness; it does not make a model call or inspect every record.

Serve behind TLS and your staff identity gateway. Use company OIDC access tokens with a required staff role, or a trusted backend that issues signed tokens after SSO and role checks. Review the SQLite handoff queue with `python -m support_agent.cli handoffs list`; connect tickets to your team workflow, backup, and retention policy. Watch for source and provider failures, overdue records, and uncertain tool calls. Keep ERP and Qdrant sync timestamps current. Use `eval --cases` with approved labeled questions and review false answers and missed handoffs before launch.

See `docs/REAL_DATA.md` for API and database contracts. No operational source, identity integration, or live provider key is bundled.
