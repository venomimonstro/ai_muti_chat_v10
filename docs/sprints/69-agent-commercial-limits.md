# Sprint 69 — Agent commercial limits & billing

Status: DONE / RUNTIME EVIDENCE

## Goal

Сделать расходы Agent Studio предсказуемыми для клиента и владельца сервиса: ограничить параллельные запуски, не допускать запуска сверх денежных лимитов и показывать пользователю фактическое потребление и остатки.

## Реализовано

- единые `max_cost_rub_per_run`, `max_cost_rub_per_day`, `max_cost_rub_per_month`;
- preflight стоимости до обращения к LLM;
- wallet/provider reservation и settle/release;
- `BUDGET_EXCEEDED` до платного provider call;
- owner-level concurrency `AGENT_MAX_ACTIVE_RUNS_PER_USER` (default 3, env-configurable);
- одинаковый concurrency enforcement для manual Agent/Team, schedule и webhook;
- автоматические trigger-ы при исчерпанной quota не создают новый платный run;
- `GET /api/v1/agents/<agent_id>/usage/`;
- Agent Studio usage panel: запуск/день/месяц, остаток и занятые слоты;
- `agent_commercial_limits_audit`;
- regression suite `test_commercial_limits.py`;
- commercial audit включён в `release_check.sh` и `update.sh`.

## Runtime evidence

Перед коммерческим запуском обязательны:

1. PostgreSQL regression suite проходит.
2. `agent_commercial_limits_audit` возвращает `AGENT_COMMERCIAL_LIMITS_AUDIT_OK` на production database.
3. Manual/schedule/webhook concurrency проверены на развернутом stack.
4. `scripts/update.sh` и финальный RC gate проходят без ошибок.
