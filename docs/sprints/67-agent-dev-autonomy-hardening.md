# Sprint 67 — Agent Studio / Dev Studio autonomy hardening

Статус: `IN PROGRESS`.

## Цель

Довести автономные запуски Agent Studio и Dev Studio до состояния, в котором расписания и внешние события безопасно переживают дубли, занятость, сбои worker/beat и ошибки конфигурации, а production deploy автоматически проверяет критические инварианты.

## Уже реализовано

- автономные расписания и календарные cadence;
- безопасная отмена AgentRun;
- event/webhook trigger с отдельным секретом;
- idempotency по Event ID;
- ограничение размера webhook payload;
- недоверенный payload отделяется от инструкции агента;
- retry при занятом агенте/команде без второго запуска;
- secret rotation;
- Agent Studio UI для webhook событий;
- `agent_system_audit`;
- `dev_studio_audit`;
- `agent_billing_audit`;
- `agent_webhook_audit`;
- production update запускает все четыре Agent/Dev аудита.

## Webhook production invariants

`agent_webhook_audit` блокирует deploy при следующих состояниях:

- trigger с двумя субъектами или без субъекта;
- trigger и агент/команда принадлежат разным владельцам;
- отсутствует hash webhook secret;
- включённый trigger ссылается на отключённого агента или остановленную команду;
- pending delivery уже содержит run;
- pending delivery завис более чем на 15 минут;
- queued delivery не имеет run;
- failed delivery не содержит причину ошибки;
- run принадлежит другому owner;
- backlink trigger/delivery/event в AgentRun противоречит delivery.

## Осталось

1. Добавить regression tests для `agent_webhook_audit`.
2. Production-like E2E: webhook invoke → Celery worker → AgentRun.
3. Проверить restart worker во время pending delivery.
4. Проверить duplicate Event ID: один run и одно списание.
5. Проверить disabled trigger / inactive agent / paused team.
6. Выполнить реальный `scripts/update.sh` на сервере и сохранить успешный вывод.

После выполнения всех пунктов Sprint 67 меняется на `DONE`, Sprint 68 — на `IN PROGRESS` в `docs/SPRINT_PLAN.md`.
