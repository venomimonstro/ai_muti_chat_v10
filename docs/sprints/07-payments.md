# Implementation brief: Sprint 7 — Payments

## Цель

Пополнение рублёвого баланса через ЮKassa без двойного зачисления, доверия к данным браузера или слепого доверия webhook.

## Реализовано

- `Payment`, `PaymentEvent`, `Refund`, `PaymentFeeVersion`, `PaymentCostSnapshot`;
- paid/promo wallet buckets и lineage в immutable ledger;
- `POST /api/v1/payments/` с обязательным `Idempotency-Key`;
- redirect confirmation URL возвращается только из server-to-server ответа ЮKassa;
- webhook endpoint `POST /api/v1/payments/webhooks/yookassa/`;
- каждый webhook перепроверяется через GET объекта у ЮKassa;
- перед зачислением сверяются provider id, статус, сумма, валюта и internal payment id;
- duplicate и reordered webhook не создают повторный credit и не понижают succeeded;
- provider `income_amount` сохраняется как фактический net cash, если доступен;
- возврат сначала блокирует доступный paid balance, затем вызывает provider;
- повтор refund/webhook не создаёт второй debit;
- management command `reconcile_payments` для незавершённых платежей;
- live gate запрещает боевые платежи при выключенной фискализации.

## Активация

По умолчанию `PAYMENTS_ENABLED=false` и `PAYMENTS_LIVE_ENABLED=false`.

До production необходимо:

1. заключить договор и получить test/live credentials;
2. выбрать подтверждённую специалистом схему 54-ФЗ;
3. настроить HTTPS webhook на 443/8443 и события `payment.succeeded`, `payment.canceled`, `refund.succeeded`;
4. пройти duplicate/reordered/timeout/refund тесты в тестовом магазине;
5. только затем включить `PAYMENTS_LIVE_ENABLED=true`.

## Инварианты

- redirect return не является доказательством оплаты;
- webhook payload не является источником истины без server-side GET;
- credit создаётся один раз по `payment.id`;
- promo нельзя вернуть как деньги;
- неизвестный статус не трактуется как неуспех;
- при HTTP 500 повторяется тот же запрос с тем же ключом.
