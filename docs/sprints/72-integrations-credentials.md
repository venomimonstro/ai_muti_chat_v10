# Sprint 72 — Integrations & credentials

Status: DONE / RUNTIME EVIDENCE

## Goal

Единая безопасная модель внешних подключений для Agent Studio с tenant isolation, health и безопасной заменой credentials.

## Реализовано

- `ExternalConnection` как единая сущность внешнего сервиса;
- credentials хранятся encrypted-at-rest и никогда не возвращаются API;
- connection owner и Agent binding tenant-isolated;
- connection health: unknown / healthy / degraded / disabled;
- явный health-check endpoint;
- failed/unchecked connection не используется для защищённой публикации;
- credential можно заменить через существующий update flow; health после изменения снова требует проверки;
- connection/binding нельзя менять во время активного Agent/Team run;
- Agent UI позволяет check, bind и unbind;
- текущая коммерческая реализация WordPress использует эту модель; новые providers должны добавляться через неё, а не отдельным хранилищем секретов;
- `connection_health_audit` проверяет encrypted credential, health consistency, stale checks и cross-tenant bindings;
- audit включён в production/release gates.

## Runtime evidence

- реальный WordPress reconnect/credential rotation;
- health transition healthy → degraded → healthy;
- production `CONNECTION_HEALTH_AUDIT_OK`.
