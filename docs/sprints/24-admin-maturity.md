# Sprint 24 — Admin Maturity

Sprint consolidates the existing finance, payments, quality, provider and support modules into
one platform-admin control plane. Every endpoint requires a staff user or an active user with
the `platform_admin` role. Mutations create immutable `AdminAuditEvent` records with actor,
target, metadata and direct request IP.

## Control planes

| Area | Endpoint | Capabilities |
| --- | --- | --- |
| Executive | `GET /api/v1/admin/overview/` | Users, organizations, 24h requests/errors, revenue, open incidents |
| Finance | `GET /api/v1/admin/finance/` | Revenue, provider cost, gross profit, liabilities, reconciliations |
| Payment Fee Matrix | `GET /api/v1/admin/payments/` | Immutable fee versions |
| Payments / Refunds | `GET /api/v1/admin/payments/` | Payment and refund inspector without raw provider payloads |
| Ledger / Wallets | `GET /api/v1/admin/ledger/` | Wallet list and user-scoped immutable ledger entries |
| Pricing / Margin Guard | `GET /api/v1/admin/pricing/` | Active prices, rules, margin policy and anomalies |
| Quality | `GET /api/v1/admin/quality/` | Eval runs, gates and model scorecards |
| Incidents | `GET /api/v1/admin/incidents/` | Provider incidents, cost anomalies and security events |
| Provider / Model Ops | `GET /api/v1/admin/providers/` | Health and current model versions |
| Mass controls | `POST /api/v1/admin/providers/bulk-action/` | Model enable/disable and provider emergency kill switch |
| Request Inspector | `GET /api/v1/admin/requests/` | Chat and B2B metadata, costs, latency and errors; no prompt text |
| Users / Organizations | `GET /api/v1/admin/users-organizations/` | Accounts, status, memberships and active key counts |
| Security / Privacy | `GET/POST /api/v1/admin/security/` | Incident registration, investigation and containment |
| Releases | `GET/POST /api/v1/admin/releases/` | Draft release registry |
| Rollout | `POST /api/v1/admin/releases/{id}/rollout/` | Canary → rolling/stable → rollback state machine |
| Backups | `GET/POST /api/v1/admin/backups/` | Backup request register |
| Restore drill | `POST /api/v1/admin/backups/{id}/action/` | Start, complete, verify and restore-drill state machine |
| Support | `GET /api/v1/admin/support/` | Cross-user support queue and status workflow |
| Feature flags | `GET/POST /api/v1/admin/feature-flags/` | Percentage rollout plus user allow/deny lists |
| Audit | `GET /api/v1/admin/audit/` | Immutable log of administrative mutations |

List endpoints support a capped `limit` parameter. Payment provider payloads, message content,
API secrets and provider credentials are deliberately excluded from inspector responses.

## Safe rollout

A release starts as `draft` and cannot jump directly to `stable`:

1. `draft → canary` with 1–10% or an explicit user allowlist;
2. `canary → rolling` with 1–99%, or `canary → stable` after health checks;
3. `rolling → stable` at 100%;
4. canary, rolling or stable may transition to `rolled_back` at 0%.

The control plane records the intended rollout and health snapshot. The deployment system must
apply the release and report its observed health; recording a release does not execute shell or
infrastructure commands from the web process.

Feature assignment is deterministic by `SHA-256(flag:user) % 100`. Deny lists override allow
lists; allow lists override percentage assignment. Runtime code can call
`apps.admin_ops.services.feature_enabled`.

## Security containment

`contain` closes a security event, blocks the affected account and revokes all active B2B keys
for organizations billed by that account in one transaction. Investigation and resolution do
not automatically alter the account.

## Backup evidence

The backup lifecycle prevents an unverified artifact from being marked as restore-tested:

`requested → running → succeeded → verified → restored`

Completion requires a private storage reference and SHA-256 checksum. The actual backup and
restore command runs outside the web process; this API stores operational evidence and audit.
