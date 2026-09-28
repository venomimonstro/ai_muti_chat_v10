# AI Workspace — единый план спринтов

Этот файл — единый источник истины по истории, текущему состоянию и пути до коммерческого запуска. Детальные документы в `docs/sprints/` и `docs/operations/` сохраняются как evidence, но статус и следующий шаг определяются здесь.

## Правила

- Номер спринта никогда не начинается заново.
- `DONE` — код и обязательные acceptance criteria завершены.
- `DONE / RUNTIME EVIDENCE` — код завершён, но production/staging evidence ещё нужно получить.
- `READY TO RUN` — реализация gate/drill завершена; следующий шаг физически выполняется на production-like сервере.
- `IN PROGRESS` — текущая разработка. Одновременно не более одного.
- `PLANNED` — ещё не начинали.
- Нельзя объявлять production launch по наличию кода: финальный PASS должен быть получен реальным запуском gate.

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

## Agent Studio / Dev Studio 67–75

| Sprint | Статус | Цель |
|---|---|---|
| 67 | DONE / RUNTIME EVIDENCE | Schedules, cancellation, webhook/event triggers, idempotency, audits |
| 68 | DONE / RUNTIME EVIDENCE | Tenant isolation, IDOR, secrets, safe tool policies, security audit |
| 69 | DONE / RUNTIME EVIDENCE | Commercial limits, run/day/month budget, concurrency, reservations, usage UX |
| 70 | DONE / RUNTIME EVIDENCE | Natural-language planner, preview, templates, readiness, zero-cost Test Mode |
| 71 | DONE / RUNTIME EVIDENCE | Dev diff/preview/approve, isolated branch, sandbox, QA, PR/merge safety, abandon recovery |
| 72 | DONE / RUNTIME EVIDENCE | Unified external connection model, encrypted credentials, health/reconnect checks |
| 73 | DONE / RUNTIME EVIDENCE | Self-service readiness/diagnostics/Test Mode and support-minimization flows |
| 74 | READY TO RUN | Worker/beat restart drill, stale-run recovery audit, production-like chaos evidence |
| 75 | READY TO RUN | Release Candidate v1.0 gate and immutable launch evidence |

Детали: `docs/sprints/67-*` … `docs/sprints/75-*`.

## Product expansion 76–79

Владелец продукта открыл следующий продуктовый цикл после Sprint 75. Это не отменяет runtime/release gates 74–75: перед заявлением о production-ready они всё равно должны дать фактический PASS.

| Sprint | Статус | Цель |
|---|---|---|
| 76 | DONE / RUNTIME EVIDENCE | Free-first web search: SearXNG + Yandex failover, diagnostics, sources |
| 77 | DONE / RUNTIME EVIDENCE | Native multimodal chat: PNG/JPEG/WebP vision, immediate-ready uploads, compare/analyze UX |
| 78 | IN PROGRESS | OpenAI Images in chat: create + edit, shared billing/storage/history, no local generation |
| 79 | PLANNED | Full client cabinet UX/reliability audit and premium consistency |

Детали: `docs/sprints/76-79-search-multimodal-images-client-ux.md`.

### Sprint 76 — реализованный код

- SearXNG — бесплатный self-hosted provider первого выбора по умолчанию.
- Yandex Search API — дополнительный provider; порядок задаётся `WEB_SEARCH_PROVIDER_ORDER`.
- Ошибка одного search provider автоматически переключает запрос на следующий.
- `web_search_diagnose` проверяет SearXNG, Yandex и общий fallback без вывода секретов.
- Regression tests фиксируют provider failover.

### Sprint 77 — реализованный код

- Существующий vision backend используется напрямую: вложения маршрутизируются только на модели с capability `vision`.
- PNG/JPEG/WebP после безопасной проверки сразу получают READY: для них не нужна асинхронная текстовая индексация.
- В composer добавлены явные действия для фото/изображений и сравнения изображений.
- Максимумы/ACL/tenant isolation существующего FileAsset + vision pipeline сохраняются.

### Sprint 78 — текущая реализация

- OpenAI Images подключён прямо к composer через существующий `image_studio`.
- Используются существующие reservation/settlement, async queue, private storage и conversation history.
- Добавлена операция OpenAI image edit для owner-scoped PNG/JPEG/WebP; source id + SHA фиксируются в immutable snapshot и перепроверяются перед provider call.
- Локальный image-generation runtime не добавляется.
- До завершения Sprint 78 нужны targeted tests/build и финальная UI-integrация редактирования/истории.

## Что реализовано к Sprint 75

### Agent Runtime

- Manual, schedule и webhook запуски используют одинаковые owner concurrency restrictions.
- Run/day/month денежные лимиты проверяются до платного provider call.
- Customer/provider spend резервируется и затем settle/release.
- Webhook idempotency и duplicate-run protection.
- Safe cancellation, approval expiry и recovery audits.
- Usage API/UI показывает фактический расход, остаток и занятые слоты.

### Agent Studio UX

- Описание сотрудника обычным языком → AI preview → просмотр workflow → draft.
- Templates, visual graph, readiness и понятные blocker actions.
- Test Mode не вызывает LLM, не списывает деньги и не выполняет внешние действия.
- Diagnostics объединяет readiness, budget, concurrency, connection health и recent failure.

### Dev Studio safety

- create/update only; delete не выполняется Dev runtime.
- Safe paths, file limits, expected SHA и повторная проверка исходного состояния.
- Exact proposed changes сохраняются в approval.
- Unified diff доступен до подтверждения.
- Sandbox до write; write только в `ai-workspace/run-*` branch.
- QA & Security + Final Review после изменения ветки.
- PR и merge привязаны к точному run/head/base/SHA; merge требует явного подтверждения.
- Abandon не выдаётся за remote deletion: default branch остаётся неизменной, isolated branch сохраняется для аудита/ручного удаления.

### Connections

- ExternalConnection — единая owner-scoped сущность подключения.
- Credentials encrypted-at-rest и не возвращаются API/агенту.
- Health check и состояния unknown/healthy/degraded/disabled.
- Connection/binding нельзя менять во время активного run.
- `connection_health_audit` контролирует tenant relationships и operational health.

### Release gates

`scripts/release_check.sh` блокирует релиз на syntax/undefined-name defects, failing PostgreSQL tests, migration drift, economic/billing failures, Agent security/commercial/recovery failures, Dev Studio audit и frontend build/lint/runtime smoke.

`scripts/agent_runtime_drill.sh` выполняет restart drill worker/beat и повторные production audits.

`scripts/agent_release_candidate_check.sh` объединяет runtime drill, существующий commercial launch gate, live paid E2E и финальные Agent/Dev audits с evidence/checksums.

## Обязательные runtime evidence / launch blockers

Наличие кода Sprint 76–79 не отменяет следующие реальные проверки:

1. `sudo bash scripts/update.sh --full` проходит полностью после завершения текущего продуктового пакета.
2. PostgreSQL regression/security/commercial tests проходят на текущем коде.
3. `agent_runtime_drill.sh` возвращает `AGENT RUNTIME DRILL: PASS` после реальных restart worker/beat.
4. Production configuration/secrets complete.
5. Real payment/refund/receipt flow verified.
6. Backup restore/application rollback drill verified.
7. Legal/provider human sign-offs complete.
8. Live dedicated E2E account configured (`E2E_USERNAME` / `E2E_PASSWORD`) with small positive balance.
9. `commercial_launch_check.sh` проходит live paid workspace AI + B2B API/billing E2E.
10. `agent_release_candidate_check.sh` завершается `AGENT RELEASE CANDIDATE v1.0: PASS`.
11. `web_search_diagnose` даёт production evidence хотя бы одного рабочего live-search provider и fallback policy.
12. OpenAI image generate/edit smoke подтверждает real provider response, billing settlement и private media delivery.

## Следующий шаг

Текущая разработка: завершить Sprint 78, затем Sprint 79. После завершения продуктового пакета выполнить targeted tests/frontend build, затем один полный release gate и runtime evidence 74–75/76–79.
