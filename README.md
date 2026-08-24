# AI Workspace / Universal AI Gateway

Единое рабочее пространство для GPT, Claude, Gemini, Grok, DeepSeek и других моделей: одна история, проекты, файлы, память и прозрачный рублёвый баланс.

Репозиторий создаётся по master-spec **AI Workspace FINAL v8**. Завершены Sprint 0–13.

## Что уже заложено

- Django API с собственной моделью пользователя и session-auth;
- persistent chat: пользовательское сообщение сохраняется до inference;
- идемпотентная отправка по `Idempotency-Key`;
- независимый `ProviderAdapter` и тестовый `EchoProviderAdapter`;
- реальный OpenAI Responses API adapter и серверный SSE streaming;
- Anthropic Messages и DeepSeek Chat adapters с единым контрактом;
- capabilities, ручной выбор модели и API версионированных цен;
- retries до первого токена, fallback-цепочки и circuit breaker;
- provider health snapshots, incident correlation и emergency kill switch;
- проекты с ACL, архивом и версионируемыми инструкциями;
- изолированные загрузки файлов, безопасная проверка MIME/magic и extraction statuses;
- извлечение и chunking для TXT/MD/CSV/DOCX/XLSX, vision-assets и честный partial для PDF;
- сохранение partial output во время генерации;
- versioned pricing и фактическая стоимость по provider usage;
- immutable ledger, reservation → settlement → release;
- раздельные paid/promo-бакеты кошелька и возвраты только из paid-баланса;
- идемпотентный lifecycle платежей и возвратов YooKassa;
- webhook с обязательной серверной перепроверкой статуса, суммы и metadata;
- запрет отрицательного доступного баланса;
- изолированный поиск по чатам, сообщениям, проектам и файлам;
- серверные и локальные черновики сообщений;
- настройки лимитов расходов, low-balance уведомления и центр уведомлений;
- смена пароля, завершение всех сессий и обращения в поддержку;
- полноценный responsive Next.js интерфейс с историей, drawers и стоимостью ответа;
- keyboard flows, доступные состояния loading/empty/error/recovery и reduced motion;
- устанавливаемый PWA shell с offline fallback;
- Universal Memory v1 explicit-first с областями пользователь / проект / чат;
- ручные команды «Запомни», «Не запоминай» и «Забудь» без скрытого автосбора;
- редактирование, архив, закрепление и отключение памяти;
- неизменяемый snapshot памяти и Context Inspector для каждого ответа;
- auto-memory candidates за серверным feature flag и пользовательским opt-in;
- локальное извлечение только из прямых сообщений пользователя без дополнительных AI-затрат;
- trust/confidence, фильтрация секретов, дедупликация и подтверждаемая замена конфликтов;
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

Следующий этап: Sprint 14 — Smart Context с rolling summary, retrieval и строгими token budgets.
