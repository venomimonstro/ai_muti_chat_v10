# Sprint 17 — Provider families и Model Registry

## Результат

Provider Gateway поддерживает пять основных семейств через единый контракт:

- OpenAI Responses API;
- Anthropic Messages API;
- DeepSeek Chat API;
- Google Gemini `streamGenerateContent`;
- xAI/Grok Chat Completions.

Все adapters нормализуют streaming в `delta` и `completed`, usage — в input/output tokens,
ошибки — в `ProviderError`. Ключи остаются только на backend: `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`. Для каждого
Provider можно задать другое имя переменной через `credential_env` и отдельный `api_base_url`.

## Безопасная смена модели

Публичный slug `AIModel` больше не обязан совпадать с изменяемым alias провайдера.
`ModelVersion` фиксирует exact API id, capabilities, routing tags, context window и output limit.
Конфигурация версии неизменяема; любое изменение оформляется новой candidate-версией.

Процесс promotion:

```bash
python manage.py register_model_version \
  --model gemini-pro \
  --version 2026-08-25 \
  --exact-api-id gemini-3.5-pro \
  --capabilities '["text", "streaming", "vision", "tools"]' \
  --context-window 1048576 \
  --max-output-tokens 65536

python manage.py run_evals \
  --model gemini-pro \
  --model-version 2026-08-25 \
  --dataset ru-core-v1 \
  --fail-on-regression

python manage.py promote_model_version \
  --model gemini-pro \
  --version 2026-08-25 \
  --eval-run <UUID> \
  --reason "ru-core-v1 gate passed"
```

Promotion отклоняется, если eval не завершён, gate не пройден, проверялась другая модель,
другая ModelVersion или другой exact API id. В одной транзакции старая версия становится
`retired`, новая — `active`, а runtime-поля `AIModel` обновляются. Router и Context Snapshot
фиксируют версию и exact API id, поэтому исторический запрос воспроизводим.

Аварийный откат использует только ранее зарегистрированную версию и обязательную причину:

```bash
python manage.py rollback_model_version \
  --model gemini-pro \
  --version 2026-07-10 \
  --reason "provider regression incident INC-42"
```

Каждый promotion/rollback записывается в `ModelVersionTransition` с исходной и целевой
версиями, eval run и причиной. Миграция создаёт активную `legacy`-версию для существующих
моделей без изменения их текущего маршрута.

## Failover и contract tests

Contract suite проверяет factory, обязательные capabilities, SSE parsing, request id и usage
в пяти семьях. Интеграционный тест выполняет retry Gemini до первого токена и fallback на
xAI; существующий тест гарантирует, что после первого delta fallback не выполняется, partial
output сохраняется, а резерв средств освобождается.

## Источники контрактов

- Google Gemini API: https://ai.google.dev/api/generate-content
- xAI streaming cost/usage: https://docs.x.ai/developers/cost-tracking

## Следующий этап

Sprint 18: Cost Protection — markup hierarchy, FX snapshots, margin floor, anomaly alerts и
reconciliation jobs.
