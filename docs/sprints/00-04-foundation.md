# Implementation brief: Sprint 0–4

## Цель

Создать воспроизводимый фундамент и доказать два главных инварианта: сообщения не теряются, деньги нельзя списать дважды.

## In scope

- окружение, CI, ADR, secrets policy;
- регистрация, вход, профиль, роли;
- provider contract и mock provider;
- conversation/message/generation;
- save-before-inference и duplicate-send protection;
- wallet, immutable ledger, reserve/settle/release;
- negative-balance и idempotency tests;
- минимальный web shell.

## Out of scope

Реальные AI API, реальные платежи, RAG, AUTO Router, организации, изображения и публичный B2B API.

## Acceptance criteria

- `docker compose up --build` поднимает систему;
- повтор одного ключа возвращает прежний результат;
- ошибка провайдера переводит generation в `failed`, освобождает резерв и сохраняет user message;
- settlement нельзя выполнить дважды;
- ledger нельзя изменить или удалить через ORM;
- пользователь не видит чужие conversation/message.

## Rollback / forward-fix

До production миграции могут откатываться штатно. После начала денежных операций финансовые таблицы исправляются только forward-fix миграциями; ledger entries никогда не переписываются.

