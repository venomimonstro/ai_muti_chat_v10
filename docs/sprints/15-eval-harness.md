# Sprint 15 — Eval Harness

Статус: завершён.

## Результат

Добавлена внутренняя eval-платформа для проверки моделей на реальных русскоязычных сценариях продукта. Она создаёт воспроизводимую связь между качеством, стоимостью и задержкой и служит обязательным gate перед будущим AUTO Router и promotion новых версий моделей.

## Taxonomy и dataset

Версионируемый набор `ru-core-v1` содержит 15 классов задач:

- бытовой Q&A;
- копирайтинг;
- редактирование;
- SEO;
- маркетинговый анализ;
- программирование;
- debugging;
- работа с таблицами;
- длинные документы;
- извлечение фактов;
- reasoning;
- research;
- структурирование;
- перевод;
- русский язык и стилистика.

Каждый `EvalCase` хранит prompt, необязательный system prompt, taxonomy, tags, минимальный балл и машиночитаемую rubric. Загрузчик идемпотентно обновляет выбранную версию и отключает удалённые из файла кейсы.

```bash
python manage.py load_eval_dataset
```

## Runner и judge

Offline runner использует единый `ProviderAdapter`, поэтому измеряет именно тот контракт, который применяется в продукте. Для каждого кейса сохраняются:

- полный ответ;
- correctness;
- instruction following;
- Russian quality;
- verbosity control;
- hallucination flag;
- latency;
- input/output tokens;
- provider cost по immutable PriceVersion;
- provider request ID и нормализованная ошибка.

Deterministic rubric judge v1 проверяет exact answer, обязательные и запрещённые фразы, структуру, лимит длины и долю русского текста. Он не вызывает дополнительную модель и не увеличивает стоимость eval. Архитектура допускает добавление blind LLM/human judge позднее.

## Воспроизводимость

`EvalRun` фиксирует snapshot slug/upstream/provider/capabilities модели. `EvalResult` фиксирует prompt, rubric, threshold и ID версии цены. Поэтому изменение Model Registry или исходного dataset не меняет историю уже выполненного прогона.

Доступ к cases, runs и scorecards через `/api/v1/eval-*` разрешён только staff-пользователям. Результаты и scorecards доступны в Django admin только для чтения.

## Scorecard и regression gates

Итоговый scorecard содержит средние значения по taxonomy и критериям, hallucination/error rate, latency, токены и стоимость. Отдельно предусмотрены показатели tool reliability, file handling и long-context stability; до появления соответствующих кейсов значение остаётся `null`, а не подменяется нулевой уверенностью.

Gate блокирует promotion, если:

- средний балл ниже минимума;
- hallucination rate выше лимита;
- error rate выше лимита;
- общий балл ухудшился относительно baseline;
- ухудшилась отдельная taxonomy.

Пороги задаются через `EVAL_MIN_AVERAGE_SCORE`, `EVAL_MAX_HALLUCINATION_RATE`, `EVAL_MAX_ERROR_RATE` и `EVAL_MAX_REGRESSION`.

```bash
python manage.py run_evals --model MODEL_SLUG --dataset ru-core-v1
python manage.py run_evals --model MODEL_SLUG --dataset ru-core-v1 \
  --baseline RUN_UUID --fail-on-regression
```

Команда с `--fail-on-regression` возвращает ненулевой exit code и может использоваться как release gate.

## Проверки

- полнота taxonomy и идемпотентность загрузчика;
- deterministic scoring и hallucination penalty;
- сбор score/cost/latency/tokens;
- immutable snapshots модели, кейса и цены;
- общий и taxonomy regression gate;
- сохранение ошибок отдельного кейса без потери всего run;
- staff-only API;
- backend lint/tests/migrations/check;
- frontend lint и production build.

## Следующий спринт

Sprint 16: AUTO Router — классификация задачи, capability filters, quality/cost/latency scoring, режимы Эконом/Баланс/Максимум и объяснение выбора пользователю.
