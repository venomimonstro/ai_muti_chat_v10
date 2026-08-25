# Sprint 16 — AUTO Router

Статус: завершён.

## Результат

Новые чаты используют AUTO Router в режиме «Баланс». Существующие чаты сохраняют ручной выбор модели для обратной совместимости. Пользователь в любой момент может выбрать:

- **Эконом** — минимальная стоимость при достаточном подтверждённом качестве;
- **Баланс** — качество, цена и скорость;
- **Максимум** — сильнейшая подходящая модель;
- **Вручную** — конкретная модель и её безопасная fallback-цепочка.

## Routing pipeline

Для каждого сохранённого сообщения выполняется:

1. локальная классификация задачи;
2. определение signals и необходимых capabilities;
3. получение включённых моделей;
4. hard filters по capabilities, context window, цене и provider health;
5. получение последней прошедшей Eval scorecard по taxonomy;
6. расчёт quality/cost/latency/health score по активной политике;
7. выбор primary route;
8. формирование ограниченной по цене fallback-цепочки;
9. сохранение `RoutingDecision`;
10. сбор Smart Context по самому узкому окну разрешённой цепочки;
11. reserve верхней стоимости и запуск provider streaming.

Классификатор v1 работает на детерминированных русскоязычных правилах. Он не вызывает LLM, поэтому сам Router не создаёт дополнительных расходов и latency.

## Task classification

Taxonomy синхронизирована с Eval Harness: Q&A, copywriting, editing, SEO, marketing, coding, debugging, spreadsheets, long documents, extraction, reasoning, research, structuring, translation и Russian style.

Дополнительные signals:

- длинный контекст;
- наличие файлов проекта;
- наличие визуальных файлов;
- необходимость vision;
- признаки задачи, требующей актуальных tools.

Текстовые file chunks уже обрабатываются Smart Context и не требуют нативной file capability upstream-модели. Vision становится hard requirement только при запросе анализа изображения и наличии изображения в проекте.

## Scoring

Активная `RoutingPolicyVersion` хранит версионируемые веса и thresholds. Политика `router-v1` использует:

| Режим | Quality | Cost | Latency | Health |
|---|---:|---:|---:|---:|
| Эконом | 0.25 | 0.55 | 0.15 | 0.05 |
| Баланс | 0.50 | 0.25 | 0.20 | 0.05 |
| Максимум | 0.75 | 0.05 | 0.15 | 0.05 |

Дополнительно учитываются routing tags, доступность tools и размер context window для длинных задач. При отсутствии собственной прошедшей eval-оценки используется явно помеченный baseline, а не выдуманная точность.

## Explainability и аудит

`RoutingDecision` неизменно фиксирует:

- версию политики;
- режим пользователя;
- taxonomy и confidence;
- signals и required capabilities;
- выбранную модель;
- всех принятых и отклонённых кандидатов с причинами;
- компоненты score и итоговый rank;
- ожидаемые токены и стоимость;
- русскоязычное объяснение выбора.

Решение также входит в `Generation.context_snapshot`. В панели «Стоимость и контекст» пользователь видит выбранную модель, режим, объяснение, confidence, оценку стоимости и сравнение кандидатов.

## Fallback и защита стоимости

Retry выполняется только до первого токена. После исчерпания retry Router переходит к следующей разрешённой модели. Кандидат, чья ожидаемая цена превышает цену primary более чем в 1.5 раза, не используется автоматически и получает причину `fallback_price_requires_consent`.

Если health провайдера изменился после preflight, недоступный кандидат исключается непосредственно перед streaming. Цены всех разрешённых кандидатов фиксируются до reserve.

## Проверки

- классификация и vision detection;
- разные решения режимов Эконом/Баланс/Максимум;
- использование прошедших eval scorecards;
- capability и provider-health hard filters;
- защита от дорогого fallback;
- сохранение explainable Routing Decision;
- совместимость со Smart Context, billing, retry/fallback, memory и idempotency;
- backend lint/tests/migrations/check;
- frontend ESLint и production build.

## Следующий спринт

Sprint 17: провайдеры 4–5, versioned Model Registry и расширенные failover contract tests.
