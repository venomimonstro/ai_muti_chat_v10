# Sprint 23 — B2B API

Sprint adds an organization-scoped public API compatible with the OpenAI Chat Completions
wire format. The organization is billed through its `billing_user` wallet, while every key
has independent scopes and safety limits.

## Public endpoints

| Method | Endpoint | Scope | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/models` | `models.read` | Available text models |
| `POST` | `/v1/chat/completions` | `chat.completions` | Text completion, including `stream=true` |
| `GET` | `/v1/usage` | `usage.read` | Current key usage for the month |

Authenticate with `Authorization: Bearer aw_live_...`. The raw secret is shown only when
the key is created or rotated; the database stores an HMAC-SHA256 digest and a lookup prefix.

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $AI_WORKSPACE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-42-attempt-1" \
  -d '{
    "model": "echo-v1",
    "messages": [{"role": "user", "content": "Составь краткое резюме"}],
    "max_completion_tokens": 512
  }'
```

Text messages with `system`, `developer`, `user`, and `assistant` roles are supported.
`developer` is normalized to `system` for providers that do not expose that role. `n` must
be `1`. `max_tokens` remains accepted as an alias for `max_completion_tokens`.

For SSE, set `"stream": true`. `stream_options.include_usage` adds the final usage chunk;
the stream always ends with `data: [DONE]`.

## Key and organization management

Authenticated workspace users manage organizations through:

- `GET/POST /api/v1/organizations/`
- `GET/POST /api/v1/organizations/{organization_id}/keys/`
- `POST /api/v1/organizations/{organization_id}/keys/{key_id}/revoke/`
- `POST /api/v1/organizations/{organization_id}/keys/{key_id}/rotate/`
- `GET /api/v1/organizations/{organization_id}/usage/`

Owner and admin roles can issue, rotate and revoke keys. Billing, developer and viewer roles
can inspect keys and usage without receiving stored secrets. All membership checks are made
server-side and cross-organization access returns `404`.

## Limits and billing

Before contacting a provider the service atomically checks key state, expiration, scopes,
allowed endpoint/model lists, optional IP/CIDR allowlist, requests per minute, concurrency,
key monthly budget and organization monthly budget. The worst-case token cost is reserved
from the organization wallet. Actual usage is settled from the immutable pricing snapshot;
provider failures release the full reservation.

`Idempotency-Key` is scoped to a key. A completed duplicate returns the original response
without a second provider call or charge. Reusing it with different input returns `409`.

Environment controls:

- `B2B_API_ENABLED`
- `B2B_API_KEY_PEPPER`
- `B2B_API_MAX_OUTPUT_TOKENS`
- `B2B_API_MAX_MESSAGE_CHARS`
- `B2B_API_RUNNING_TIMEOUT_SECONDS`
