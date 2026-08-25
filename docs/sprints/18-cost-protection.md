# Sprint 18 — Cost Protection

## Результат

Price Engine защищает деньги владельца до запуска provider request и сохраняет полностью
воспроизводимый snapshot для settlement и последующей сверки.

## Иерархия цены

`MarkupRuleVersion` применяется в фиксированном порядке:

1. global;
2. provider;
3. model;
4. operation;
5. organization;
6. contract.

Более конкретный `markup_percent` заменяет предыдущий. `price_multiplier` перемножается и
поддерживает договорный коэффициент. Каждое применённое правило сохраняется в
`RequestCost.pricing_snapshot`; изменение настроек влияет только на новые запросы.

## FX snapshots

`PriceVersion` хранит provider currency и исходные цены за миллион токенов.
`FxRateSnapshot` неизменяем и фиксирует пару, курс, источник, reference и effective time.
Для RUB используется системный identity snapshot `RUB/RUB = 1`. Для внешней валюты отсутствие
явного курса блокирует расчёт:

```bash
python manage.py record_fx_rate \
  --base USD \
  --rate 82.8004 \
  --source cbr/manual-verified \
  --reference "source document or rate id"
```

При preflight в snapshot сохраняются PriceVersion, FX id/rate, ModelVersion, применённые markup
rules, effective markup, multiplier, operation type и MarginPolicyVersion. Settlement использует
этот snapshot, а не текущий курс или новые правила.

## Margin Guard

`MarginPolicyVersion` задаёт минимальную gross margin, допустимое превышение ожидаемой
себестоимости и порог reconciliation. По умолчанию floor равен 25%.

- AUTO Router исключает route с маржой ниже floor;
- ручной запрос не проходит preflight, пока цена не исправлена новой rule version;
- уже начатые запросы не пересчитываются;
- фактическое нарушение floor или превышение cost ceiling создаёт deduplicated `CostAnomaly`.

## Reconciliation

Ежедневная задача сверяет:

- cached wallet balances с immutable ledger и paid/promo buckets;
- `RequestCost` с Generation settlement;
- фактический cost/charge с повторным расчётом по immutable pricing snapshot;
- model slug PriceVersion с реально routed model.

Статусы: `ok`, `undercharged`, `overcharged`, `provider_mismatch`, `manual_review`. Исправление
денежных записей автоматически не выполняется: расхождение создаёт anomaly для расследования.

Ручной запуск:

```bash
python manage.py reconcile_billing
```

Celery Beat запускает `daily_financial_reconciliation` раз в сутки. При включённых платежах эта
же задача проверяет открытые YooKassa payments. В Docker Compose добавлен отдельный `beat` process.

## Owner finance API

Staff-only endpoint `GET /api/v1/finance/summary/` возвращает usage revenue, provider cost,
gross profit/margin, liability пользовательских балансов, число открытых anomalies и последнюю
reconciliation. Секреты и пользовательский контент endpoint не раскрывает.

## Следующий этап

Sprint 19: RAG v1 — chunking pipeline, pgvector, metadata ACL, citations и prompt-injection
defenses.
