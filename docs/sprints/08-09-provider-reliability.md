# Implementation brief: Sprint 8–9 — Provider Reliability

## Цель

Подключить несколько независимых AI-провайдеров и не терять запрос, ответ или деньги при временном сбое upstream.

## Sprint 8

- единый adapter contract для OpenAI Responses, Anthropic Messages и DeepSeek Chat;
- нормализация typed SSE, usage и provider request id;
- capabilities и routing tags в реестре моделей;
- ручной выбор только существующей и доступной модели;
- `GET /api/v1/models/` возвращает capabilities, доступность, health и активную цену;
- fallback-модель задаётся в реестре и не требует изменений frontend;
- секреты читаются только из server-side environment.

## Sprint 9

- retry выполняется только до первого полученного токена;
- после частичного ответа автоматический повтор запрещён, чтобы не склеить два разных ответа;
- fallback использует заранее зарезервированный максимум стоимости всей цепочки;
- каждый вызов записывается как `GenerationAttempt` с provider, model, latency и error code;
- circuit открывается после настраиваемого числа retryable failures;
- успешная проба закрывает circuit и отмечает открытый incident как recovered;
- `emergency_disabled` немедленно исключает провайдера из новых запросов;
- health snapshots создаются командой `check_provider_health`;
- rollback-only команда `synthetic_chat_journey` проверяет save → reserve → stream → settle;
- пользователь получает безопасное recovery-сообщение и correlation id вместо сырой ошибки API;
- `/api/v1/health/` используется как liveness, `/api/v1/readiness/` проверяет БД.

## Команды эксплуатации

```bash
python manage.py check_provider_health
python manage.py synthetic_chat_journey
```

## Переменные окружения

- `AI_PROVIDER_MAX_ATTEMPTS`;
- `AI_CIRCUIT_FAILURE_THRESHOLD`;
- `AI_CIRCUIT_COOLDOWN_SECONDS`;
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`;
- соответствующие `*_API_BASE_URL` для тестового gateway или прямого API.

## Инварианты

- retry/fallback после первого токена не выполняется;
- недоступный или аварийно отключённый provider не получает новый запрос;
- стоимость считается по price snapshot реально выбранной fallback-модели;
- fallback не может превысить сумму, зарезервированную до inference;
- исходное пользовательское сообщение сохраняется до любого внешнего вызова;
- сырые provider errors и credentials не возвращаются пользователю.
