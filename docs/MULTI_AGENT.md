# Supply chain multi-agent mode

Set `APP_MODE=production`, `AGENT_DOMAIN=supply_chain`, `AI_MODE=groq`, and `SUPPLY_AGENT_MODE=multi` with `GROQ_API_KEY` and `OPENROUTER_API_KEY`. Configure exactly one real supply source as described in `docs/REAL_DATA.md`.

The supervisor routes to inventory, procurement, logistics, or guidance tools. Tools only read current records. Groq is primary; OpenRouter handles transient provider failure. Tool arguments and returned records are validated by application code. Actions, stale data, overdue dates, provider failures, and uncertainty go to a person. The response includes a decision explanation generated after validation, with source IDs and checked steps. Never treat the model's own prose as proof of a tool result.

Run `doctor`, then evaluate redacted real questions with `eval --cases`. Unit tests simulate providers; live accuracy and fallback behavior need to be verified with your keys.
