# Dev Studio v2 — Sprint 81–90

Этот документ фиксирует следующий цикл разработки после Sprint 80. Он дополняет `docs/SPRINT_PLAN.md`; production-ready статус по-прежнему требует реального runtime evidence, а не только наличия кода.

## Цель цикла

Сделать Dev Studio не цепочкой LLM-ответов, а управляемой средой разработки: Director планирует работу как DAG, Developer работает с реальным snapshot проекта, код проверяется до approval и перед write, выполнение наблюдаемо, восстановление после сбоев fail-closed, а расходы и provider failover остаются атомарными.

| Sprint | Статус | Область | Acceptance |
|---|---|---|---|
| 81 | DONE / RUNTIME EVIDENCE | Dev Workspace Runtime | bounded repository discovery, persistent isolated workspace API, safe patch/delete/move, Python/Node/JSON checks, dedicated sandbox image/network |
| 82 | DONE / RUNTIME EVIDENCE | Engineering Director 2.0 | validated DAG, dependencies, roles, acceptance criteria, task-aware execution, duplicate/cycle rejection |
| 83 | DONE / RUNTIME EVIDENCE | Coding loop & guarded changes | create/update/delete with snapshot SHA, aggregate file cap, pre-approval validation, repeated pre-write validation, isolated branch |
| 84 | DONE / RUNTIME EVIDENCE | Dev Command Center | live Director plan, real step state mapping, per-role cost, workspace/branch/applied-files verification evidence |
| 85 | IN PROGRESS | Test/evidence depth | project-aware safe checks where workspace completeness allows; explicit proof instead of agent assertions |
| 86 | DONE / RUNTIME EVIDENCE | Crash/restart recovery | stale Dev execution detection, operator-only fail-closed repair, reservation cleanup, workspace cleanup, recovery evidence |
| 87 | IN PROGRESS | Provider continuity & economics | healthy API-key retry within same priced model, exact-key degradation, single settlement, attempts evidence; cross-model fallback only when accounting contract is safe |
| 88 | PLANNED | Dev security hardening | prompt-injection boundaries, path/symlink abuse, generated-secret scan, dependency/script execution policy, audit expansion |
| 89 | PLANNED | Commercial E2E Dev journey | bootstrap → plan → changes → preview → approve → sandbox → branch → QA → PR, cancellation/recovery/budget scenarios |
| 90 | PLANNED | Dev Studio RC gate | dedicated blocking gate + production-like evidence bundle/checksums and release decision |

## Реализовано в Sprint 81

- `dev_context.py` делает bounded scan исходников и конфигурации вместо фиксированного набора манифестов.
- Vendor/build/cache каталоги исключаются из контекста.
- Workspace API поддерживает sync/patch/run/destroy.
- Sandbox не открывает arbitrary shell: исполняются только whitelist-команды через `shell=False` и resource limits.
- Production использует отдельный `Dockerfile.sandbox`, read-only filesystem, tmpfs workspace, dropped capabilities и internal-only `sandbox_net`.
- Backend readiness проверяет реальный `/health` sandbox, а не только наличие shared secret.

## Реализовано в Sprint 82

- Engineering Director выдаёт машиночитаемый DAG.
- План валидирует ID, роли, зависимости, self-reference, duplicate nodes и cycles.
- Architecture/Development получают конкретную задачу Director и acceptance criteria.
- UI показывает план и связывает task ID с фактическим `AgentStepRun.node_id`.
- Параллельное выполнение не включено поверх общего ORM/billing-контекста: оно будет допустимо только для действительно изолированных worker/workspace jobs.

## Реализовано в Sprint 83

- Developer contract поддерживает `create`, `update`, `delete`.
- `update/delete` требуют файл из реально прочитанного snapshot и его expected SHA.
- Нельзя молча обрезать change-set: >12 файлов отклоняют весь proposal.
- Несколько Development-задач не могут конфликтующе менять один путь.
- Для реального repository context change-set проигрывается в preview workspace до создания approval.
- Preview workspace удаляется после проверки.
- После пользовательского approval код повторно проверяется на свежем snapshot перед созданием рабочей ветки — защита от TOCTOU.
- Write выполняется только в `ai-workspace/run-*`; default branch не изменяется напрямую.

## Реализовано в Sprint 84

- Run page показывает Director DAG, роль, зависимости, acceptance и фактическое состояние node.
- Отдельная Verification panel показывает workspace ID, working branch, applied files и результаты sandbox checks.
- Стоимость видна по фактически завершённым платным шагам.
- `provider_attempts` сохраняется в step evidence.

## Sprint 85 — текущая граница

Whitelist sandbox уже содержит `pytest`, `django-check`, `npm-test`, `npm-build`, `npm-lint`, но автоматический запуск project-level test suite разрешается только когда workspace содержит достаточный snapshot проекта. Нельзя выдавать неполный bounded context за полный checkout и получать ложные FAIL/PASS.

Следующий технический шаг Sprint 85: ввести признак completeness/manifest contract и выбирать test matrix на его основании; для неполного snapshot оставлять syntax/structural checks и явно показывать их уровень доказательства.

## Реализовано в Sprint 86

- `dev_studio_recover` в dry-run режиме только обнаруживает stale runs и блокирует молчаливое игнорирование.
- `--repair` используется оператором после проверки worker state.
- WAITING_APPROVAL не восстанавливается автоматически: решение пользователя может ждать долго и не является зависанием worker.
- PLANNING/RUNNING/WAITING_TOOL/REVIEWING старше порога переводятся в `failed/dev_runtime_interrupted`.
- Активные steps становятся FAILED с явным recovery log.
- Освобождаются активные customer reservations с idempotency prefix конкретного run и provider reservations с его source prefix.
- Workspace уничтожается best-effort; рабочая ветка сохраняется в recovery evidence для аудита.
- Никакой provider/GitHub action не переигрывается автоматически.

## Sprint 87 — реализованная часть

- Dev LLM stages используют retry по healthy ключам того же provider/model через `generate_with_key_failover`.
- Использованный проблемный ключ деградируется exact-adapter attribution.
- Новый adapter выбирает следующий healthy key.
- Customer/provider reservation создаётся один раз и settle выполняется один раз после успешного ответа.
- Число попыток сохраняется как `provider_attempts`.
- Межмодельный fallback сознательно не подключён напрямую к этому пути, пока для каждой альтернативной модели не будет пересчитана цена/маржа/reservation. Иначе возможна неверная экономика.

## Gates

`scripts/dev_studio_check.sh` является targeted blocking gate цикла 81–90. Он проверяет:

- Python syntax;
- production compose syntax;
- test stack с настоящим sandbox service;
- Director/changes/preapproval/provider retry/recovery/sandbox/cancel/budget/safety regressions;
- Django checks и migration drift;
- `dev_studio_audit`;
- dedicated sandbox image build;
- frontend production builder + lint.

Главный `scripts/release_check.sh` также поднимает sandbox и содержит критические Dev Studio v2 regressions.

## Runtime evidence, которое ещё обязательно

1. `sudo bash scripts/dev_studio_check.sh` → `DEV_STUDIO_V2_CHECK=PASS` на production-like host.
2. `sudo bash scripts/release_check.sh` → `RELEASE CHECK: PASS` на том же commit.
3. Реальный Dev project: Director plan → code proposal → preapproval PASS → approval → sandbox PASS → GitHub isolated branch → QA/Final Review.
4. Kill/restart worker drill во время Dev execution и проверка `dev_studio_recover` без двойных charge/commit.
5. Key-pool failure drill: первый credential падает, второй healthy завершает stage, ledger содержит одно успешное списание.
6. После Sprint 89 — полный коммерческий E2E сценарий и evidence bundle Sprint 90.
