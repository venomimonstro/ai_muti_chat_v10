# Sprint 26 - One-command installation and stability audit

Цель спринта - превратить готовый продукт в систему, которую можно безопасно установить на
обычный сервер без ручной сборки инфраструктуры, и устранить дефекты, найденные расширенным
аудитом конкурентности, идемпотентности, платежей и эксплуатации.

## Результат

- `install.sh` - интерактивная и возобновляемая установка;
- `scripts/update.sh` - обновление с обязательным backup перед миграциями;
- Caddy ingress и automatic TLS;
- production static serving и health-gated startup;
- непривилегированные backend/frontend containers;
- initial administrator bootstrap command;
- automatic stale-operation recovery;
- scoped idempotency для tenant isolation;
- single-runner claim для SSE generation;
- atomic payment refund and Compare lifecycle;
- Django/Next security patch upgrades;
- audit report и regression tests.

GitHub Actions и другие CI workflow удалены по решению владельца. Проверки выполняются локально
перед прямой публикацией в `main`.
