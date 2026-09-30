# Dev Studio v2 — Sprint 81–90

Цикл 81–90 переводит Dev Studio из линейной цепочки LLM-ответов в управляемую инженерную среду. Кодовый цикл завершён; production-ready статус ставится только после фактического PASS targeted/release/RC gates на сервере.

| Sprint | Статус | Область | Acceptance |
|---|---|---|---|
| 81 | CODE COMPLETE / RUNTIME GATE | Dev Workspace Runtime | bounded repository discovery, persistent isolated workspace API, safe patch/delete, Python/Node/JSON checks, dedicated sandbox image/network |
| 82 | CODE COMPLETE / RUNTIME GATE | Engineering Director 2.0 | validated DAG, dependencies, roles, acceptance criteria, task-aware execution, duplicate/cycle rejection |
| 83 | CODE COMPLETE / RUNTIME GATE | Coding loop & guarded changes | create/update/delete with snapshot SHA, aggregate cap, pre-approval + pre-write validation, isolated branch |
| 84 | CODE COMPLETE / RUNTIME GATE | Dev Command Center | live Director plan, real node state mapping, per-role cost, workspace/branch/applied-files verification evidence |
| 85 | CODE COMPLETE / RUNTIME GATE | Project-aware verification | syntax checks for bounded snapshots; django/pytest project checks only for proven complete snapshots; explicit evidence level |
| 86 | CODE COMPLETE / RUNTIME GATE | Crash/restart recovery | stale execution detection, operator-only fail-closed repair, reservation/workspace cleanup, recovery evidence |
| 87 | CODE COMPLETE / RUNTIME GATE | Provider continuity & economics | healthy-key retry, accounting-safe cross-model fallback, per-model pricing/reservations, atomic settle, interrupted-settlement recovery |
| 88 | CODE COMPLETE / RUNTIME GATE | Security hardening | untrusted repository context boundary, generated-secret guard, risk classification, path/SHA controls, approvals |
| 89 | CODE COMPLETE / LIVE E2E REQUIRED | Commercial E2E | completed Dev Run evidence: DAG → change-set → approval → sandbox → isolated branch → QA → Final Review → no active reservations |
| 90 | CODE COMPLETE / LIVE RC REQUIRED | Dev Studio RC | targeted gate + runtime audit + stale-run audit + commercial E2E audit + immutable SHA-256 evidence manifest |

## Execution plane

- `dev_context.py` делает bounded scan реальных исходников и конфигурации; vendor/build/cache каталоги исключаются.
- Workspace API поддерживает sync/patch/run/destroy.
- Sandbox не открывает arbitrary shell: только whitelist-команды, `shell=False`, resource limits, read-only container и internal-only network.
- Backend readiness делает реальный `/health` probe sandbox до запуска Dev Team.
- Полный snapshot получает project-aware `django-check`/`pytest`; неполный snapshot получает только безопасные bounded/syntax checks с явным evidence level.

## Engineering Director / coding loop

- Director выдаёт валидируемый DAG с зависимостями, ролями и acceptance criteria.
- Циклы, неизвестные зависимости, duplicate/self-reference блокируются до исполнения.
- Developer поддерживает create/update/delete; update/delete требуют файл из прочитанного snapshot и expected SHA.
- Change-set больше лимита отклоняется целиком; конфликтующие Development-задачи не могут молча менять один путь.
- Change-set проверяется в preview workspace до approval и повторно перед GitHub write.
- GitHub write выполняется только в `ai-workspace/run-*`; default branch напрямую не меняется.

## Reliability / provider continuity

- Same-model retry использует следующий HEALTHY API key и exact-key degradation.
- Cross-model fallback планируется только по реально доступным кандидатам и пересчитывает собственную цену/маржу каждой модели.
- Каждый model attempt имеет отдельные customer/provider idempotency keys и отдельный reserve.
- Failed provider attempt освобождает только свой reserve; successful attempt settle происходит один раз.
- Provider + customer settlement после delivery выполняются одной DB-транзакцией.
- После подтверждённого provider response settlement failure никогда не маскируется release-ом: сохраняются request/tokens/PriceVersion/reservation IDs и run получает `dev_settlement_interrupted`.
- `dev_studio_reconcile --run-id <id> --apply` восстанавливает только финансовый settlement по сохранённому evidence и не повторяет LLM/GitHub side effects.
- `dev_studio_runtime_audit` блокирует RC, пока существует незакрытый `dev_settlement_interrupted`.

## Security boundary

- System message содержит только trusted role/tool/task contracts.
- Repository/README/source comments и previous-agent outputs передаются отдельным `UNTRUSTED WORKING CONTEXT` и не получают system priority.
- High-confidence secrets/private keys блокируются до approval.
- Dependency/deployment/migration/delete/money-sensitive изменения получают risk evidence и видны пользователю до подтверждения.
- Path traversal, snapshot SHA и isolated-branch contracts остаются fail-closed.

## Recovery

- `dev_studio_recover` dry-run обнаруживает stale runs.
- `--repair` завершает stale execution fail-closed, освобождает однозначно безопасные резервы и удаляет ephemeral workspace best-effort.
- Provider/GitHub actions не переигрываются автоматически после неопределённого worker interruption.
- WAITING_APPROVAL не считается зависанием worker.

## UI evidence

Run page показывает:

- Director DAG и фактические node states;
- рабочую ветку и workspace ID;
- applied files;
- sandbox/project checks с PASS/FAIL;
- evidence level (`syntax_bounded_snapshot`, `syntax_complete_snapshot`, `project_checks_complete_snapshot`);
- стоимость по фактически завершённым платным шагам;
- provider/model attempts и fallback evidence.

## Blocking gates

### Targeted

`sudo bash scripts/dev_studio_check.sh`

Проверяет shell/Python syntax, production compose, настоящий sandbox test service, Dev regressions, cross-model settlement integrity, Django/migration drift, runtime audits, sandbox image и frontend build/lint.

Успешный маркер:

`DEV_STUDIO_V2_CHECK=PASS`

### Общий release

`sudo bash scripts/release_check.sh`

Полный backend PostgreSQL/pgvector suite также включает новые Dev Studio tests, затем экономические/billing/security/recovery gates и frontend production build/runtime smoke.

### Sprint 89/90 commercial RC

После одного реального завершённого Dev Run:

`DEV_RUN_ID=<uuid> sudo bash scripts/dev_studio_release_candidate_check.sh`

RC требует:

1. targeted Dev Studio PASS;
2. production runtime audit PASS;
3. отсутствие stale execution;
4. E2E evidence: isolated branch, Director DAG, approval, sandbox PASS, applied files, QA & Security, Final Review, GitHub operation audit;
5. отсутствие активных customer/provider reservations по run;
6. immutable evidence log + SHA-256 + JSON manifest с commit SHA и Dev Run ID.

## Production acceptance

Код Sprint 81–90 считается завершённым. Production acceptance наступает только после фактических серверных результатов:

1. `DEV_STUDIO_V2_CHECK=PASS`;
2. `RELEASE CHECK: PASS`;
3. реальный Dev Run завершён через approval/sandbox/branch/QA/Final Review;
4. `DEV STUDIO RELEASE CANDIDATE: PASS` на том же commit;
5. evidence manifest/checksum сохранены в `logs/`.
