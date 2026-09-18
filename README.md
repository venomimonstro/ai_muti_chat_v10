# AI Workspace / Universal AI Gateway

Единое рабочее пространство для GPT, Claude, Gemini, Grok, DeepSeek и других моделей: одна история, проекты, файлы, память, AUTO Router и прозрачный рублёвый баланс.

Проект прошёл инженерные и коммерческие спринты до production-hardening. Технический запуск разрешается только после прохождения release/commercial gates и реальных production sign-off.

## Основные возможности

- Django API с собственной моделью пользователя и session-auth;
- persistent chat: пользовательское сообщение сохраняется до inference;
- идемпотентная отправка по `Idempotency-Key`;
- OpenAI, Anthropic, DeepSeek, Gemini и xAI adapters;
- AUTO Router: Быстро / Баланс / Максимум;
- capability/context/provider-health routing и controlled fallback;
- проекты, файлы, память, PDF/RAG, vision и web search;
- локальный multilingual semantic retrieval через pgvector;
- versioned pricing, FX, markup и margin guard;
- immutable ledger, reservation → settlement → release;
- paid/promo balance и YooKassa lifecycle;
- OpenAI-compatible B2B API, API keys, ACL, budgets, RPM/concurrency limits;
- публичный коммерческий сайт, auth/recovery, клиентский workspace и Admin Console;
- русскоязычный Admin Console: пользователи, провайдеры, цены, финансы, платежи, безопасность, поддержка, операции, аналитика, проверки запуска;
- автоматический системный анализ и журнал багов: HTTP 5xx, worker/Celery, frontend JavaScript errors, provider/payment/AI health;
- persistent bug registry в PostgreSQL, correlation IDs, группировка повторов и lifecycle «открыта → разбираемся → исправлена»;
- ротационный JSONL аварийный журнал с маскированием распространённых секретов;
- backups, restore/rollback drills, chaos/load/E2E gates;
- production Docker Compose, Caddy HTTPS и однокомандный installer.

## Установка production одной командой

Сначала направьте A/AAAA DNS-запись домена на сервер, затем на чистом Ubuntu/Debian выполните:

```bash
curl -fsSL https://raw.githubusercontent.com/venomimonstro/ai_muti_chat_v10/main/scripts/one_click_install.sh | sudo bash
```

Bootstrap сам:

- установит необходимые системные пакеты;
- скачает проект в `/opt/ai-workspace`;
- проверит RAM, диск и порты;
- установит Docker Compose v2 при необходимости;
- создаст production secrets;
- соберёт контейнеры;
- применит миграции;
- создаст администратора;
- запустит PostgreSQL, Redis, backend, Celery worker/beat, frontend и Caddy;
- получит HTTPS;
- проверит readiness.

Повтор той же команды после прерванной установки безопасно продолжает процесс. Подробности: `docs/operations/installation.md`.

После установки:

```bash
cd /opt/ai-workspace
sudo bash scripts/system_diagnostics.sh
sudo bash scripts/commercial_launch_check.sh
```

`commercial_launch_check.sh` не выдаст `PASS`, пока не пройдены кодовые тесты, production E2E, системный health gate, платежные/провайдерские проверки, backups/drills и обязательные compliance sign-offs.

## Локальная разработка

```bash
cp .env.example .env
docker compose up --build
```

- приложение: http://localhost:3000
- API: http://localhost:8000/api/v1/
- Django admin: http://localhost:8000/admin/

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
6. Секреты провайдеров не передаются во frontend и маскируются в диагностике.
7. Сбой одного AI-запроса или UI-модуля не должен ронять весь пользовательский workspace.
8. Известная незакрытая production-ошибка блокирует коммерческий launch gate.
9. Destructive migration не проходит обычный production deploy.
10. Коммерческий запуск считается разрешённым только после `COMMERCIAL LAUNCH: PASS`.
