# Adversarial economics, security and competitor-pain simulation

This document is a production acceptance matrix. A commercial release must fail closed when any P0 invariant below is not satisfied.

## Product invariants

1. **No customer credit.** Provider work must not start until the maximum customer charge is reserved atomically. The wallet, paid bucket, promo bucket and active reservations must remain non-negative and reconcilable from the immutable ledger.
2. **Provider overrun is the service's incident, not customer debt.** If actual provider usage exceeds the pre-authorized maximum, the customer is not charged above the reservation. Record the provider cost, open a critical anomaly, disable the loss-making provider and release the customer's reservation.
3. **No silent charge on failed work.** Provider/network/internal failure before a completed billable result releases the reservation. A partial chat response is visibly marked partial/failed.
4. **Stuck operations cannot hold money forever.** Celery Beat runs stale-operation recovery every five minutes. Stale chat, Compare, Compare synthesis, image, B2B and file-processing operations are terminalized safely; active reservations are released where appropriate.
5. **Price is visible before expensive operations.** Image and Compare flows preview expected RUB cost and require explicit confirmation over configured thresholds. Text generation reserves a conservative worst-case amount and settles from provider-reported usage.
6. **A pricing error cannot scale indefinitely.** Negative margin/provider-cost-above-charge creates a critical cost anomaly and emergency-disables the provider. The economic safety gate blocks release while a critical-loss provider remains enabled.
7. **B2B cannot be unlimited accidentally.** Active API keys must have either a key-level or organization-level spend limit. Concurrency/RPM controls and idempotency remain additional protections, not substitutes for a money ceiling.
8. **Every money mutation is idempotent and auditable.** Credits, reservations, releases and debits use unique idempotency keys; ledger entries are immutable.

## Adversarial economic simulations

| Scenario | Expected behavior | Release evidence |
| --- | --- | --- |
| User funds 1,000 RUB, sends many concurrent requests totaling 10,000 RUB | Row lock + reserve allow at most the funded amount; later requests fail before provider call | `test_adversarial_economics.py` |
| Provider reports more usage than reserved | Never create customer debt; customer reservation is released; provider loss recorded; provider emergency-disabled | chat streaming + cost anomaly tests |
| Duplicate/retried HTTP request | Same idempotency key returns/reuses one operation; no duplicate provider/billing operation | chat/image/B2B idempotency tests |
| Worker dies after reserve | Beat recovery terminalizes stale operation and releases reservation | `test_stale_recovery_economics.py` |
| Compare synthesis dies after reserve | Stale synthesis reservation released and synthesis becomes retryable | `test_stale_recovery_economics.py` |
| DB/service code tries to write negative wallet | Database CHECK constraints reject mutation | `test_adversarial_economics.py` |
| Cached wallet differs from ledger | Reconciliation/economic gate reports mismatch; no silent auto-rewrite of money | billing reconciliation tests |
| B2B API key created without any spend ceiling | `economic_safety_check` blocks commercial release | `test_adversarial_economics.py` |
| Provider price/FX configuration produces guaranteed loss | Margin gate blocks quote or loss circuit disables provider | billing cost-protection tests |
| Failed image generation after reserve | Reservation released, failed result marked, unsafe/partial generated files removed | Image Studio tests |

## Data-leak and security simulations

- Conversation, search and conversation-assets endpoints filter by authenticated owner and soft-delete visibility.
- Chat asset inventory must never return another user's messages, links, files or generated images.
- Generated image source endpoint checks generation owner and only serves completed images with private cache headers and `nosniff`.
- Uploaded files are isolated by user/project path, type/size/archive limits and prompt-injection metadata.
- GitHub binding is project-ACL protected. Another user receives 404 instead of repository metadata.
- GitHub App user/PAT tokens are not persisted. Server creates short-lived installation tokens scoped to one repository and only the required `contents` permission.
- Repository paths reject traversal (`..`, empty segments, dot segments), binary/oversized files are rejected, writes require an exact existing SHA and explicit confirmation.
- GitHub write permission is disabled by default per project and all reads/writes are logged.
- Admin/security secrets remain environment/server-secret configuration, not returned through ordinary client APIs.

## Competitor pain acceptance criteria

Public Russian-language reviews in 2026 repeatedly describe these pain classes across AI aggregators: opaque internal-token burn, rapid exhaustion of paid limits, automatic renewals/refund disputes, paid features becoming unavailable, unstable or stuck generation and slow/template support. These reports are anecdotes rather than controlled benchmarks, but they are useful negative product requirements.

Our acceptance criteria are therefore:

- show RUB cost/upper bound before expensive actions instead of making the user mentally convert opaque credits;
- show per-operation actual cost and usage after completion;
- never permit a negative wallet or post-factum customer debt;
- never hide a failed generation as a successful paid operation;
- automatically recover stuck reservations;
- retain a searchable history of chat messages, generated images, files and links;
- if a provider/model is removed or disabled, do not silently pretend it is still available;
- support must have correlation IDs, operation state and billing evidence so a disputed charge can be investigated;
- subscription/autorenewal, if introduced later, must be explicit and independently cancellable; do not make it the only way to buy usage;
- refunds and payment status must be documented and visible rather than handled only by support chat.

## Chat materials UX

Every conversation has a resource inventory endpoint and UI panel:

- **Images:** all completed image generations explicitly attached to that conversation; searchable by prompt/model.
- **Files:** files actually used as conversation attachments; searchable by filename.
- **Links:** unique HTTP(S) links found in messages plus web-search source links; searchable by URL/title.

The inventory counts all resource types even when the UI is currently displaying only one tab. ACL tests cover cross-user isolation.

## GitHub development mode

A project can bind one GitHub repository through a GitHub App. The browser supports directories and UTF-8 text files. Read access uses a repository-scoped installation token. Editing is deliberately two-stage: the user first enables write access for the project, then every file update requires the current SHA and an explicit confirmation. This prevents blind overwrite of concurrent GitHub changes and limits blast radius if an AI action is wrong.

## Release gates

Before commercial deployment run `scripts/release_check.sh`. In addition to lint/tests/migration/frontend checks it runs `python manage.py economic_safety_check`. Production Celery Beat must be running; otherwise stale-operation recovery is not active and the deployment is not commercially safe.
