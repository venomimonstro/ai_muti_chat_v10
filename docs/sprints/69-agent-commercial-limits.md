# Sprint 69 — Agent commercial limits & billing

Status: IN PROGRESS

## Goal

Сделать расходы Agent Studio предсказуемыми для клиента и владельца сервиса: ограничить параллельные запуски, не допускать запуска сверх денежных лимитов и показывать пользователю фактическое потребление и остатки.

## Уже существовало до Sprint 69

- `max_cost_rub_per_run`, `max_cost_rub_per_day`, `max_cost_rub_per_month` у Agent;
- preflight стоимости до обращения к LLM;
- wallet reservation до provider call;
- provider funding reservation;
- settle/release после результата или ошибки;
- `BUDGET_EXCEEDED` без вызова модели при превышении доступного бюджета;
- запрет второго активного run одного и того же агента/команды.

## Реализовано в Sprint 69

- единый owner-level concurrency limit `AGENT_MAX_ACTIVE_RUNS_PER_USER` (default 3);
- `ensure_owner_run_capacity()` как fail-closed проверка запуска;
- commercial usage snapshot с run/day/month spent, limits, remaining;
- effective remaining budget;
- owner active runs / available slots;
- `GET /api/v1/agents/<agent_id>/usage/` для прозрачного UI;
- enforcement для безопасного ручного запуска Agent и Team;
- regression tests для concurrency и usage snapshot.

## Осталось до DONE / RUNTIME EVIDENCE

- применить owner concurrency к scheduled team runs и webhook team runs;
- убедиться, что automated triggers при исчерпании owner concurrency не создают новый платный run;
- добавить `agent_commercial_limits_audit` в production update gate;
- выполнить PostgreSQL regression suite и production-like release gate;
- подключить usage endpoint к Agent Studio UI без дублирования расчётов на frontend.

## Acceptance criteria

Sprint можно перевести в `DONE / RUNTIME EVIDENCE`, когда все способы запуска (manual/schedule/webhook) используют одинаковые коммерческие ограничения, тесты подтверждают отсутствие второго запуска сверх quota, а production audit видит некорректные лимиты и oversubscription.
