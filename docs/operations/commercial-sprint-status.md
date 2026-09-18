# Commercial Release Roadmap — implementation status

This document is the source of truth for commercial hardening after Sprint 27. Do not reopen completed scope unless a regression or failed release gate proves it is necessary.

## Status definitions

- `DONE`: code and launch controls are implemented; release gate must still execute on a real checkout.
- `DONE_WITH_RUNTIME_EVIDENCE`: implementation is complete but production evidence is intentionally required before launch.
- `PARTIAL`: remaining product integration is explicitly listed.

## Sprint status

| Sprint | Status | Implemented |
|---|---|---|
| 27 Commercial Bootstrap | DONE | Provider/model/price bootstrap, routing/margin/FX defaults, provider health checks, freshness-aware commercial config command, installer integration |
| 28 Token Accounting | DONE | Calibrated token estimator, context preflight, token-aware reserve; B2B context is also rejected before reservation when it exceeds the selected model window |
| 29 AUTO Router v2 | DONE | Task classification, capability signals, quality/cost/latency routing policy and upgrade command |
| 30 PDF Production | DONE | PDF text layer, page provenance/citations, encrypted/page-limit/scanned-PDF handling; scanned PDFs are explicitly partial until OCR is added rather than silently misread |
| 31 Semantic RAG v2 | DONE | multilingual E5/FastEmbed embeddings, pgvector hybrid retrieval, safe lexical fallback for legacy hash vectors, active-file-only reindex and runtime semantic-model readiness gate |
| 32 Vision E2E | DONE | image attachments, ACL/limits, vision-aware routing, provider-specific image payloads |
| 33 Web Search & Tools | DONE | configurable SearXNG search, SSRF/DNS controls, citations, immutable web context and strict remaining-token budget enforcement |
| 34 Core AI UX | PARTIAL | Safe dependency-free Markdown renderer and immutable edit/regenerate backend exist. Main legacy `app/page.tsx` still renders message text directly and should be split before large UI refactors. |
| 35 Accounts & Authentication | DONE | email verification, forgot/reset password, session list/revoke, TOTP MFA, recovery codes, isolated MFA secrets, admin MFA enforcement and recovery pages |
| 36 Anti-Abuse | DONE | verified-email promo issuance, abuse velocity scan for chat/B2B usage and hourly Celery scheduling |
| 37 Billing Production | DONE_WITH_RUNTIME_EVIDENCE | strict YooKassa/fiscalization/fee gate plus fresh payment reconciliation requirement; requires real production payment/refund/receipt evidence |
| 38 Provider Price Watcher | DONE_WITH_RUNTIME_EVIDENCE | immutable pricing/FX snapshots, anomaly detection, explicit apply, native-price to RUB conversion using current FX, old active price retirement; real provider source feeds remain deployment-specific |
| 39 Media Storage & Backup | DONE_WITH_RUNTIME_EVIDENCE | media backup/restore, SHA256, offsite rclone flow with byte-for-byte remote verification; production remote must be configured and restore drill recorded |
| 40 Deployment & Rollback | DONE_WITH_RUNTIME_EVIDENCE | release gate before update, migration safety, DB+media backup and app rollback; rollback drill must be executed and recorded |
| 41 Release Gate | DONE | local Docker PostgreSQL/pgvector stack, secret scan, shell/Compose syntax, backend lint/tests, migration drift, frontend build/lint; internal shell calls do not depend on executable file modes |
| 42 Observability | DONE | correlation IDs, structured request logs and runtime-safe operational metrics endpoint |
| 43 Load & Chaos | DONE_WITH_RUNTIME_EVIDENCE | bounded load smoke, Redis/backend/worker chaos recovery checks and stronger wallet/reservation financial invariants |
| 44 Legal & Commercial Layer | DONE_WITH_RUNTIME_EVIDENCE | compliance manifest, legal templates, provider-specific terms signoffs and strict launch blockers; documents require legal review and publication evidence |
| 45 Commercial UX & Onboarding | DONE | pricing/FAQ/getting-started pages, onboarding API and actual activation/payment funnel endpoint |
| 46 Final Commercial Launch Audit | DONE_WITH_RUNTIME_EVIDENCE | unified `commercial_launch_audit`, release gate chaining, semantic-model runtime check, machine-readable evidence JSON and SHA256 |

## Remaining engineering work before declaring the codebase completely closed

1. Finish Sprint 34 integration by replacing direct `{message.content}` rendering in the legacy chat page with `MarkdownMessage`, expose edit/regenerate actions in that page, and then split the monolithic page into feature components.
2. Run `bash scripts/release_check.sh` on a real Docker checkout and fix every failure; repository tooling cannot substitute for execution evidence.
3. Run staging load/chaos, payment/refund/receipt, backup restore and rollback drills and record the resulting evidence with `record_operational_drill`.
4. Configure SMTP, AI provider credentials/models/prices, YooKassa production credentials, offsite backup target and all provider-specific commercial terms signoffs.
5. Run `bash scripts/commercial_launch_check.sh`. Public paid traffic is allowed only after it exits `0` and the evidence JSON/hash are retained.

## Non-blocking follow-up

The explicit-model B2B `/v1` endpoint is financially safe on upstream failure (reservation is released and no charge is taken), but it does not currently reroute a caller who requested a specific model to a different model when that provider circuit is open. Keep that behavior explicit rather than silently substituting a model; a future provider-alias/failover contract can be added separately if desired.

## Launch rule

Never bypass a failed gate by changing the gate to `PASS`. Fix the underlying condition or attach reviewed evidence through the existing signoff model. New providers require their own `provider-terms-<slug>` approval before strict prelaunch can pass.
