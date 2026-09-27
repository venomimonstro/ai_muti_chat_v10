# Sprint 74 — Agent/Dev staging E2E, load & chaos

Status: READY TO RUN

## Goal

Проверить не happy path, а восстановление системы после реальных сбоев worker/beat и зависших операций.

## Реализовано

- `agent_recovery_audit` блокирует release при stale active Agent/Dev runs;
- recovery window учитывает `max_runtime_seconds` агента; для Dev Team используется отдельное окно;
- `scripts/agent_runtime_drill.sh` выполняет baseline audits;
- drill перезапускает worker и повторяет все Agent/Dev audits;
- drill перезапускает beat и повторяет audits;
- в финале проверяет обязательные production services и backend readiness;
- в drill входят webhook/security/commercial/billing/recovery/connection/Dev Studio audits;
- release/update gates содержат recovery checks.

## Обязательная runtime evidence

Запустить на staging/production-like stack:

```bash
sudo bash scripts/agent_runtime_drill.sh
```

Успех только при `AGENT RUNTIME DRILL: PASS`. Дополнительно финальный RC должен покрыть live provider outage/failure paths через существующие regression/E2E gates и сохранить evidence logs.
