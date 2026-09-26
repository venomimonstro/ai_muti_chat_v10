# AI Workspace — единый план спринтов

Этот файл — единый источник истины по истории, текущему спринту и плану до коммерческого запуска. Старые детальные документы в `docs/sprints/` и `docs/operations/` сохраняются как доказательство реализации, но статус и следующий номер спринта определяются здесь.

## Правила

- Номер спринта никогда не начинается заново.
- `DONE` — код реализован.
- `DONE / RUNTIME EVIDENCE` — код готов, но перед production нужны реальные проверки/доказательства.
- `IN PROGRESS` — текущий спринт. Одновременно должен быть только один.
- `PLANNED` — ещё не начинали.
- Новый функционал сначала добавляется сюда, затем реализуется.
- Нельзя помечать спринт `DONE`, если его обязательные acceptance criteria не выполнены.

## История 00–26

| Sprint | Статус | Область |
|---|---|---|
| 00–04 | DONE | Foundation |
| 05–06 | DONE | Provider pricing |
| 07 | DONE | Payments |
| 08–09 | DONE | Provider reliability |
| 10 | DONE | Projects & Files |
| 11 | DONE | UX hardening |
| 12 | DONE | Explicit memory |
| 13 | DONE | Auto memory |
| 14 | DONE | Smart context |
| 15 | DONE | Eval harness |
| 16 | DONE | AUTO Router |
| 17 | DONE | Provider families & model versioning |
| 18 | DONE | Cost protection |
| 19 | DONE | RAG v1 |
| 20 | DONE | Semantic search |
| 21 | DONE | Compare & branches |
| 22 | DONE | Images |
| 23 | DONE | B2B API |
| 24 | DONE | Admin maturity |
| 25 | DONE | Release hardening |
| 26 | DONE | One-command installer & stability |

Детали: `docs/sprints/`.

## Commercial hardening 27–46

| Sprint | Статус | Область |
|---|---|---|
| 27 | DONE | Commercial bootstrap |
| 28 | DONE | Token accounting |
| 29 | DONE | AUTO Router v2 |
| 30 | DONE | PDF production |
| 31 | DONE | Semantic RAG v2 |
| 32 | DONE | Vision E2E |
| 33 | DONE | Web Search & Tools |
| 34 | PARTIAL | Core AI UX; legacy chat integration remained |
| 35 | DONE | Accounts & Authentication |
| 36 | DONE | Anti-abuse |
| 37 | DONE / RUNTIME EVIDENCE | Billing production |
| 38 | DONE / RUNTIME EVIDENCE | Provider price watcher |
| 39 | DONE / RUNTIME EVIDENCE | Media storage & backup |
| 40 | DONE / RUNTIME EVIDENCE | Deployment & rollback |
| 41 | DONE | Release gate |
| 42 | DONE | Observability |
| 43 | DONE / RUNTIME EVIDENCE | Load & chaos |
| 44 | DONE / RUNTIME EVIDENCE | Legal & commercial layer |
| 45 | DONE | Commercial UX & onboarding |
| 46 | DONE / RUNTIME EVIDENCE | Final commercial launch audit |

Исторический источник: `docs/operations/commercial-sprint-status.md`.

## Commercial application 47–66

| Sprint | Статус | Область |
|---|---|---|
| 47 | DONE | Public commercial website |
| 48 | DONE | Auth & Registration UX |
| 49 | DONE | Client Workspace Professional UX |
| 50 | DONE | Frontend architecture cleanup |
| 51 | DONE | Customer account cabinet |
| 52 | DONE | Wallet & payment UX |
| 53 | DONE | Admin Console foundation |
| 54 | DONE | Admin executive dashboard |
| 55 | DONE | Admin users & support |
| 56 | DONE | Admin providers/models/pricing |
| 57 | DONE | Admin finance & payments |
| 58 | DONE | Admin operations & security |
| 59 | DONE | Analytics & commercial funnel |
| 60 | DONE | SEO & commercial content |
| 61 | DONE / RUNTIME EVIDENCE | Mobile & cross-browser QA contract |
| 62 | DONE / RUNTIME EVIDENCE | Commercial E2E contract |
| 63 | DONE / CONFIG REQUIRED | Production configuration |
| 64 | DONE / RUNTIME EVIDENCE | Production drills |
| 65 | DONE / HUMAN SIGN-OFF | Legal & provider sign-off |
| 66 | READY TO RUN | Final commercial launch gate |

Исторический источник: `docs/operations/commercial-launch-sprint-status.md`.

## Текущий этап Agent Studio / Dev Studio

| Sprint | Статус | Цель |
|---|---|---|
| 67 | DONE / RUNTIME EVIDENCE | Agent/Dev Studio autonomy hardening: schedules, safe cancellation, event/webhook triggers, idempotency, production audits |
| 68 | IN PROGRESS | Agent tenant isolation & security red-team: IDOR, cross-tenant references, secret exposure, SSRF/tool abuse, sandbox boundaries |
| 69 | PLANNED | Agent commercial limits & billing: quotas, per-run/per-day caps, concurrency, reservations, transparent cost UX |
| 70 | PLANNED | Agent Studio self-service UX: natural-language creation, generated plan review, test mode, templates, understandable errors |
| 71 | PLANNED | Dev Studio production safety: diff/preview/approve, branch lifecycle, rollback/recovery, destructive-action barriers |
| 72 | PLANNED | Integrations & credentials: unified connection model, permission scopes, health status, reconnect/rotation flows |
| 73 | PLANNED | Support minimization: onboarding wizard, contextual help, diagnostics, knowledge base, operator runbooks |
| 74 | PLANNED | Agent/Dev staging E2E, load & chaos: worker/beat restart, provider outage, duplicate events, stuck runs, restore/rollback |
| 75 | PLANNED | Release Candidate v1.0: clean install, upgrade, regression/security checks, payment + first-agent journey, final launch evidence |

## Sprint 67 — code complete, runtime evidence pending

Completed in code:

- agent schedules and calendar cadence;
- safe run cancellation;
- secure/idempotent event webhook triggers;
- webhook secret rotation;
- Agent Studio webhook UI;
- duplicate-run protection and busy retry behavior;
- `agent_system_audit`, `dev_studio_audit`, `agent_billing_audit`;
- dedicated `agent_webhook_audit` in production update gate;
- regression tests for disabled triggers, inactive-agent delivery, worker idempotency, stale delivery audit and cross-tenant webhook corruption.

Runtime evidence still required before commercial launch:

- webhook invoke → Celery → AgentRun lifecycle on PostgreSQL/Redis production-like stack;
- worker restart during pending webhook delivery;
- duplicate Event ID never starts a second run or second charge under real concurrency;
- disabled trigger / inactive agent / paused team failure paths on deployed stack;
- successful `scripts/update.sh` output with all Agent/Dev audits.

## Sprint 68 acceptance criteria

Completed in code so far:

- negative IDOR tests for Agent CRUD/run;
- negative IDOR tests for run detail/cancel/repeat;
- negative IDOR tests for team and webhook management;
- existing serializer ownership validation confirmed for project/director references;
- fail-closed code-writing policy: `github=true`, `shell=sandbox`, `merge=approval`;
- `agent_security_audit` checks Agent, Version, Team, Member, Run, Step, Approval, Handoff, Artifact, Schedule and Webhook tenant relationships;
- webhook secrets checked as recognized password hashes;
- `agent_security_audit` added to production update gate;
- dedicated `docs/sprints/68-agent-tenant-security.md` created.

Still required before Sprint 68 becomes `DONE / RUNTIME EVIDENCE`:

- execute Agent security regression suite on the production-like PostgreSQL stack;
- execute `python manage.py agent_security_audit` against the real server database;
- execute `scripts/update.sh` successfully with the new security gate;
- retain security/runtime evidence for launch audit.

## Launch blockers independent of sprint number

Commercial launch is blocked until all of the following have runtime evidence where required:

1. `bash scripts/release_check.sh` returns success.
2. Production configuration and secrets are complete.
3. Real payment/refund/receipt flow is verified.
4. Backup restore and application rollback drills are verified.
5. Agent/Dev Studio production-like E2E and failure scenarios pass.
6. Legal/provider sign-offs are complete.
7. `bash scripts/commercial_launch_check.sh` returns `PASS`.

## How to continue development

At the start of every development session:

1. Read this file.
2. Find the single `IN PROGRESS` sprint.
3. Work only on its remaining acceptance criteria unless a P0 regression blocks the product.
4. After implementation, update the same sprint here.
5. Only after all acceptance criteria pass, mark it `DONE` or `DONE / RUNTIME EVIDENCE` and change the next `PLANNED` sprint to `IN PROGRESS`.

This prevents an AI coding agent or a human developer from inventing a new numbering scheme or reimplementing already completed scope.
