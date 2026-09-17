# Sprint 27 — Commercial Bootstrap

Sprint 27 prepares a clean installation for safe commercial configuration without enabling paid traffic accidentally.

## What is automated

`python manage.py bootstrap_catalog` is idempotent and creates:

- provider templates for OpenAI, Anthropic, DeepSeek, Gemini and xAI;
- one disabled model placeholder per provider;
- a model version only when the matching `*_DEFAULT_MODEL` environment variable is explicitly configured;
- a price version only when both matching RUB-per-million price variables are explicitly configured;
- default AUTO routing weights for Economy / Balanced / Maximum;
- a global markup rule;
- a margin policy;
- the RUB/RUB identity FX snapshot.

Providers and models remain disabled after bootstrap. This is intentional: a fresh installation must never start sending paid traffic before credentials, model IDs and pricing have been reviewed.

## Provider environment variables

Each provider uses a server-side credential variable and optional default model ID:

- `OPENAI_API_KEY`, `OPENAI_DEFAULT_MODEL`
- `ANTHROPIC_API_KEY`, `ANTHROPIC_DEFAULT_MODEL`
- `DEEPSEEK_API_KEY`, `DEEPSEEK_DEFAULT_MODEL`
- `GEMINI_API_KEY`, `GEMINI_DEFAULT_MODEL`
- `XAI_API_KEY`, `XAI_DEFAULT_MODEL`

Secrets are never returned by the Admin Ops API. The API only reports whether the referenced environment variable is configured.

## Bootstrap prices

Bootstrap accepts reviewed provider-cost prices in RUB per one million tokens:

- `AI_PRICE_OPENAI_DEFAULT_INPUT_RUB_PER_MILLION`
- `AI_PRICE_OPENAI_DEFAULT_OUTPUT_RUB_PER_MILLION`
- equivalent `ANTHROPIC`, `DEEPSEEK`, `GEMINI`, and `XAI` variables.

Blank values do not create an active price. `commercial_config_check` requires positive input and output prices for every enabled model.

## Admin setup API

Staff-only endpoints:

- `GET /api/v1/admin-ops/commercial-setup/` — setup status without secret values;
- `POST /api/v1/admin-ops/commercial-setup/` with `{ "action": "bootstrap" }` — rerun idempotent bootstrap;
- `POST /api/v1/admin-ops/commercial-setup/providers/<slug>/health/` — validate the configured provider credential and update provider health.

## Commercial gate

Before enabling paid traffic run:

```bash
python manage.py commercial_config_check --require-healthy
```

The command blocks launch if:

- routing, markup, margin or RUB identity configuration is missing;
- no model is enabled;
- an enabled model belongs to a disabled provider;
- an enabled provider has no server-side credential;
- an enabled model has no upstream model ID or active version;
- an enabled model has no positive active input/output price;
- `--require-healthy` is used and the provider has not passed its latest health check.

The production installer now runs `bootstrap_catalog` automatically after migrations. It does not enable external AI providers or payments.
