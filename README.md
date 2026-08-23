# AI Workspace / Universal AI Gateway

Единое рабочее пространство для GPT, Claude, Gemini, Grok, DeepSeek и других моделей: одна история, проекты, файлы, память и прозрачный рублёвый баланс.

Репозиторий создаётся по master-spec **AI Workspace FINAL v8**. Завершён backend-контур Sprint 0–6.

## Что уже заложено

- Django API с собственной моделью пользователя и session-auth;
- persistent chat: пользовательское сообщение сохраняется до inference;
- идемпотентная отправка по `Idempotency-Key`;
- независимый `ProviderAdapter` и тестовый `EchoProviderAdapter`;
- реальный OpenAI Responses API adapter и серверный SSE streaming;
- сохранение partial output во время генерации;
- versioned pricing и фактическая стоимость по provider usage;
- immutable ledger, reservation → settlement → release;
- запрет отрицательного доступного баланса;
- базовый Next.js интерфейс чата;
- Docker Compose: PostgreSQL, Redis, backend, worker, frontend;
- CI: backend lint/tests и frontend lint/build;
- ADR и implementation brief для текущего этапа.

## Быстрый запуск

```bash
cp .env.example .env
docker compose up --build
```

- приложение: http://localhost:3000
- API: http://localhost:8000/api/v1/
- Django admin: http://localhost:8000/admin/

Создать администратора:

```bash
docker compose exec backend python manage.py createsuperuser
```

## Локальная разработка backend

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py migrate
pytest
```

## Критические инварианты

1. Сообщение пользователя сохраняется до вызова AI-провайдера.
2. Одинаковый `Idempotency-Key` не создаёт повторное списание.
3. Ledger entries не изменяются и не удаляются.
4. Баланс реконструируется суммой ledger entries.
5. Ни один запрос не имеет неограниченную стоимость.
6. Секреты провайдеров не передаются во frontend и не пишутся в логи.

Следующий этап: Sprint 7 — платёжный lifecycle и идемпотентные webhook без подключения боевых платежей до юридической проверки.
