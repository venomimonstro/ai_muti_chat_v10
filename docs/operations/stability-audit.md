# Аудит стабильности и безопасности - Sprint 26

Дата: 2026-08-26. Объём: Django API, Next.js frontend, billing/payments, AI adapters,
background jobs, files, production Docker topology, установка и обновление.

## Исправленные критические и высокие риски

| Риск | Последствие | Исправление |
|---|---|---|
| Next.js 16.3.2 с критическими advisory | компрометация frontend/runtime | обновление до security release 16.3.3 |
| Django 5.2.5 ниже актуального LTS patch | известные security defects | обновление до Django 5.2.17 |
| Глобальная идемпотентность чатов/Compare/платежей | коллизии и отказ запросов между клиентами | ключи ограничены владельцем/платежом, добавлены DB constraints |
| Два SSE-клиента могли запустить одну генерацию дважды | двойной provider cost и гонка settlement | атомарный claim `queued → running` |
| Параллельные возвраты не блокировали Payment | превышение суммы возврата | `select_for_update`, atomic refund lifecycle |
| Зависшие операции удерживали balance reservation | баланс и B2B budget блокировались навсегда | recovery task каждые 5 минут и ручная команда |
| Compare мог остаться RUNNING при ошибке reserve/finalize | зависший запуск и деньги | атомарная инициализация и аварийный release |
| CSRF token кэшировался после login rotation | первый POST после входа получал 403 | сброс клиентского token cache после auth transitions |
| Status endpoint падал вместе с БД | отсутствие статуса во время аварии | degraded response без повторных DB queries, отдельный Redis probe |
| Preflight ошибка streaming оставляла wallet reservation | баланс блокировался после сбоя до inference | release в except и recover_stranded_reservations |
| RELEASED reservation блокировала retry с тем же ключом | retry/idempotency не списывали средства | re-activate RELEASED reservation в reserve() |
| Compare synthesis не переживал retry | одна ошибка блокировала синтез | очистка synthesis_reservation_id и select_for_update |
| B2B/image idempotency после FAILED | клиент получал 409 или stale FAILED | retry с тем же Idempotency-Key |
| Frontend mintил новые idempotency keys | двойные списания при повторе send | reuse client_message_id/idempotency до успешного stream |
| B2B org budget race (parallel keys) | превышение monthly cap организации | `select_for_update` на Organization в `_begin()` |
| `is_staff` открывал admin API | privilege escalation | только `role=PLATFORM_ADMIN` |
| B2B IP spoofing при прямом доступе к backend | bypass allowlist | XFF только от trusted proxy peers |
| Session fixation на login/register | hijack сессии | `session.cycle_key()` после auth |
| Слабые production defaults | компрометация при misconfig | fail-fast при DEBUG=false |
| Conversation/project write через read_only | IDOR при создании чата в чужом проекте | корректные read_only_fields в serializers |
| Production topology не имела ingress/TLS/static | приложение недоступно или небезопасно | Caddy, automatic HTTPS, static volume, health dependencies |
| Backend/frontend запускались root | усиление последствий container escape | непривилегированные runtime users |

## Исправленные средние риски

- password validators применяются при регистрации и смене пароля;
- login/register имеют отдельные throttling scopes;
- readiness проверяет БД и Redis, а не только SQL;
- B2B IP allowlist получает реальный IP только через явно включённый trusted proxy mode;
- oversized base64 image отклоняется до декодирования;
- файлы неудачной image generation удаляются, storage leak устранён;
- malformed YooKassa payload возвращает контролируемую ошибку вместо 500;
- Docker logs ротируются, процессы получают grace period и init;
- `.env.production`, backups и installer markers исключены из Git;
- HSTS не захватывает все поддомены без отдельного решения владельца;
- CSP frontend больше не разрешает произвольные HTTPS endpoints для `connect-src`.

## Автовосстановление

Команда `python manage.py recover_stale_operations` и Celery Beat закрывают старые:

- chat generations;
- Compare runs;
- image generations;
- B2B API usages;
- file parsing jobs.

Reservation освобождается идемпотентно, операция получает конечный failed/partial state. Дополнительно `recover_stranded_reservations()` освобождает активные резервации у уже failed chat generations.

## Проверки

- полный backend test suite;
- Django system check и migration drift check;
- frontend ESLint, TypeScript и production build;
- shell syntax install/update/backup/restore scripts;
- JSON consistency package-lock;
- production settings check с временными безопасными значениями;
- high-confidence tracked-secret scan.

## Остаточные launch blockers

Это не программные ошибки и они не могут быть закрыты автоматически:

- реальное MFA для администраторов через выбранный identity layer;
- договоры и коммерческие условия AI-провайдеров;
- ЮKassa, чеки, налоговый режим и процесс возвратов;
- политика обработки персональных данных и публичные документы;
- restore drill на реальном PostgreSQL backup;
- DNS, firewall и внешний мониторинг production-сервера.
