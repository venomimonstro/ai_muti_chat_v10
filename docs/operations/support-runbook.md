# Support runbook

## Intake

Classify requests as access, payment/refund, balance/cost, provider failure, privacy/data request
or product guidance. Confirm the account by authenticated request context; never ask for a
password, API key, card data or provider credential.

## Safe investigation

- Balance: use Wallet/Ledger Inspector and reconciliation evidence. Never rewrite a balance.
- Request cost: use correlation ID, immutable pricing snapshot and provider usage.
- Payment: use payment ID and authoritative provider status; do not trust webhook text alone.
- Refund: confirm unused paid balance and reuse the original idempotency key on retry.
- Privacy: record scope and identity verification; export/delete follows the approved legal flow.
- AI failure: share only safe error codes. Prompts and files remain private unless the user has
  explicitly provided the relevant content in the support request.

Escalate suspected abuse/data exposure to SecurityEvent immediately. Update support status to
`in_progress`, record external evidence outside free-text secrets, then mark `resolved` only after
the user-facing outcome is verified.
