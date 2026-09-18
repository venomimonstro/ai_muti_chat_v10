# Установка AI Workspace одной командой

Поддерживается чистый сервер Ubuntu/Debian с root/sudo, минимум ~2 ГБ RAM и 10 ГБ свободного диска. Рекомендуется от 4 ГБ RAM. Порты 80 и 443 должны быть свободны, DNS домена должен указывать на сервер.

## Интерактивная установка

```bash
curl -fsSL https://raw.githubusercontent.com/venomimonstro/ai_muti_chat_v10/main/scripts/one_click_install.sh | sudo bash
```

Bootstrap сам:

1. установит `git`, `curl`, сертификаты и необходимые системные пакеты;
2. скачает проект в `/opt/ai-workspace`;
3. установит Docker/Compose при необходимости;
4. проверит RAM, диск и порты 80/443;
5. запросит домен, email для TLS и данные администратора;
6. сгенерирует PostgreSQL/Redis/Django/B2B/MFA secrets;
7. создаст `.env.production` с правами `600`;
8. соберёт контейнеры;
9. поднимет PostgreSQL и Redis;
10. выполнит миграции и Django checks;
11. создаст начальный каталог AI-провайдеров и администратора;
12. запустит backend, worker, beat, frontend и Caddy;
13. дождётся HTTPS и `/api/v1/readiness/`;
14. выведет адреса сайта, ЛК, Admin Console и диагностики.

## Полностью non-interactive вариант

```bash
curl -fsSL https://raw.githubusercontent.com/venomimonstro/ai_muti_chat_v10/main/scripts/one_click_install.sh | sudo env \
  AIWS_NONINTERACTIVE=true \
  AIWS_DOMAIN=ai.example.ru \
  AIWS_ACME_EMAIL=admin@example.ru \
  AIWS_ADMIN_USERNAME=admin \
  AIWS_ADMIN_EMAIL=admin@example.ru \
  AIWS_ADMIN_PASSWORD='CHANGE_ME_STRONG_PASSWORD' \
  bash
```

Если `AIWS_ADMIN_PASSWORD` не задан, установщик сгенерирует пароль и выведет его один раз в конце.

## После установки

Базовая диагностика:

```bash
cd /opt/ai-workspace
sudo bash scripts/system_diagnostics.sh
```

Полный release gate:

```bash
sudo bash scripts/release_check.sh
```

Финальная коммерческая проверка:

```bash
sudo bash scripts/commercial_launch_check.sh
```

Установка приложения и коммерческая готовность — разные состояния. Установщик намеренно **не** включает платные AI-модели и live-платежи без реальных API-ключей, цен, SMTP, реквизитов продавца, YooKassa, MFA и юридических подтверждений.

## Обновление

После успешной установки повторно bootstrap запускать не нужно:

```bash
cd /opt/ai-workspace
sudo bash scripts/update.sh
```

## Журнал системных ошибок

HTTP 5xx и ошибки Celery автоматически группируются в Admin Console:

`/admin-console/system`

Persistent JSONL-журнал хранится в Docker volume `logs_data` по пути контейнера:

`/app/logs/system_issues.jsonl`

Он не содержит request body, пароли или API-ключи; фиксируются тип исключения, безопасное краткое сообщение, маршрут/задача, correlation/task ID и traceback.
