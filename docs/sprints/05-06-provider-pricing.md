# Implementation brief: Sprint 5–6

## Цель

Подключить первый реальный AI provider через заменяемый адаптер, сохранять поток ответа и рассчитывать списание по фактическому provider usage.

## Реализовано

- OpenAI Responses API adapter с `store=false`;
- typed SSE parsing: delta/completed/error;
- серверный SSE endpoint `POST /api/v1/conversations/{id}/messages/stream/`;
- пользовательское сообщение и Generation создаются до upstream request;
- периодическое сохранение partial output;
- immutable `PriceVersion`;
- `RequestCost` с estimate/provider cost/final charge;
- markup и округление вверх до 0,0001 ₽;
- conservative reservation до начала inference;
- release всего резерва при provider failure;
- повторный Idempotency-Key не создаёт вторую генерацию или стоимость.
- PostgreSQL torture test проверяет 20 параллельных попыток резерва при балансе только на 10;
- аномальный provider usage сверх резерва блокируется без debit;
- повторные release/settle безопасны.

## Настройка первого provider

1. В admin создать Provider с adapter type `OpenAI Responses API`, URL `https://api.openai.com/v1` и credential env `OPENAI_API_KEY`.
2. Создать AIModel, указать внутренний slug и upstream model.
3. Создать активный PriceVersion. Старые версии не редактируются.
4. Передать `OPENAI_API_KEY` только backend/worker окружению.

Цены намеренно не зашиты в код: каждый запрос закрепляет конкретную версию цены, а изменение тарифа создаёт новую запись.

## Out of scope

Автоматический fallback, circuit breaker, ЮKassa и websocket resume входят в следующие спринты.
