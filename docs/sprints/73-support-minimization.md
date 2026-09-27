# Sprint 73 — Support minimization

Status: DONE / RUNTIME EVIDENCE

## Goal

Большинство проблем пользователь должен диагностировать и исправить сам без обращения к владельцу сервиса.

## Реализовано

- существующий readiness показывает blockers, warnings и конкретные действия;
- новый `GET /agents/<id>/diagnostics/` объединяет readiness, budget, concurrency, connections и последнюю ошибку run;
- diagnostics возвращает безопасные user-facing issue codes и ссылки на исправление;
- credentials и внутренние секреты в diagnostic response не попадают;
- отдельный Agent Diagnostics UI component подготовлен для интерфейса;
- Test Mode позволяет проверить workflow без затрат и внешних действий;
- run history и public logs остаются источником объяснения фактически выполненных действий;
- regression test подтверждает tenant isolation diagnostics.

## Support policy

1. Сначала readiness/diagnostics/Test Mode.
2. Затем конкретный failed run и его public error.
3. Только если автоматическая диагностика не даёт действия — обращение в поддержку.
4. Оператор не просит у клиента passwords/API secrets: reconnect выполняется пользователем через connection UI.

## Runtime evidence

Проверить типовые сценарии: нет бюджета, занят concurrency slot, сломано подключение, provider outage, неготовый workflow, последний failed run.
