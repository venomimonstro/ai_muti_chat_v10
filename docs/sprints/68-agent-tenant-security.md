# Sprint 68 — Agent tenant isolation & security red-team

## Цель

Сделать Agent Studio и Dev Studio fail-closed по границам пользователей: знание UUID чужого объекта не должно давать чтение, изменение, запуск или косвенную ссылку на чужие данные; опасные инструменты разработки не должны обходить sandbox и approval workflow.

## Реализовано

- отдельный `agent_security_audit` для production/release проверки tenant-инвариантов;
- проверка `Agent -> Project`, `AgentVersion -> creator`, `Team -> director/project`, `TeamMember -> agent/project`;
- проверка `AgentRun -> owner/subject/project`;
- проверка `AgentStepRun`, approvals, handoffs и artifacts на cross-run/cross-tenant ссылки;
- проверка schedules и webhook triggers/deliveries на совпадение tenant owner;
- проверка, что webhook secret хранится как password hash, а не открытым текстом;
- fail-closed policy для code-writing агентов: обязателен GitHub, `shell=sandbox`, `merge=approval`;
- неизвестные инструменты и неподдерживаемые shell/merge режимы запрещаются существующим validator;
- security audit включён в `scripts/update.sh`, поэтому повреждение tenant-инвариантов блокирует production update;
- regression tests на IDOR для agent CRUD/run, run detail/cancel/repeat, team и webhook management;
- regression tests на cross-tenant data corruption и unsafe Dev Studio tool policy.

## Security invariants

1. Любой пользовательский Agent/Team/Run/Schedule/Webhook доступен только владельцу.
2. UUID объекта не является авторизацией.
3. Ссылки между tenant-owned моделями не могут пересекать владельцев.
4. Webhook secret никогда не возвращается после создания/rotation и хранится только в хешированном виде.
5. Агент с `write_code=true` не может выполнять host shell или делать прямой merge.
6. Code execution разрешён только через sandbox; GitHub merge — только через approval workflow.
7. Production deploy должен завершаться ошибкой, если `agent_security_audit` обнаружил нарушение.

## Проверки перед завершением спринта

На реальном checkout выполнить:

```bash
cd /opt/ai-workspace
sudo docker compose --env-file .env.production -f docker-compose.prod.yml run --rm backend pytest apps/agents/test_tenant_security.py apps/agents/test_webhook_hardening.py apps/agents/test_webhooks.py -q
sudo docker compose --env-file .env.production -f docker-compose.prod.yml run --rm backend python manage.py agent_security_audit
sudo bash scripts/update.sh
```

## Acceptance criteria

- [x] owner-scoped negative API tests добавлены;
- [x] cross-tenant model graph audit добавлен;
- [x] webhook secret-at-rest audit добавлен;
- [x] code-writing tool policy переведена в fail-closed режим;
- [x] production update запускает security audit;
- [ ] regression suite успешно выполнен на PostgreSQL production-like stack;
- [ ] `agent_security_audit` возвращает `AGENT_SECURITY_AUDIT_OK` на сервере;
- [ ] `scripts/update.sh` проходит полностью после изменений.

Пока последние три runtime-пункта не подтверждены, Sprint 68 остаётся `IN PROGRESS`.
