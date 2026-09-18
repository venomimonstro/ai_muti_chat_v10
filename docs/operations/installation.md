# Установка AI Workspace на сервер

## Что потребуется

- чистый сервер Ubuntu 22.04/24.04 или актуальный Debian;
- минимум 2 ГБ RAM, рекомендуется 4 ГБ и выше;
- минимум 10 ГБ свободного диска, для коммерческой эксплуатации рекомендуется 30 ГБ и выше;
- домен с A/AAAA-записью на IP сервера;
- свободные входящие порты 80 и 443.

## Установка одной командой

На чистом сервере выполните:

```bash
curl -fsSL https://raw.githubusercontent.com/venomimonstro/ai_muti_chat_v10/main/scripts/one_click_install.sh | sudo bash
```

Bootstrap сам установит `git/curl/openssl`, скачает проект в `/opt/ai-workspace` и запустит мастер production-установки.

Мастер спросит только данные, которые нельзя безопасно придумать автоматически:
- домен;
- email для HTTPS-сертификата;
- логин и email первого администратора;
- пароль администратора или предложит безопасно сгенерировать его.

Установщик автоматически:

1. проверяет Ubuntu/Debian, RAM, свободный диск и занятость портов 80/443;
2. устанавливает Docker и Docker Compose v2, если их нет;
3. генерирует отдельные секреты Django, PostgreSQL, Redis, MFA и B2B API;
4. создаёт `.env.production` с правами `0600`;
5. собирает production backend/frontend;
6. запускает PostgreSQL и Redis и ждёт их readiness;
7. применяет миграции и выполняет Django system check;
8. создаёт начальный каталог моделей и первого platform administrator;
9. запускает backend, worker, beat, frontend и Caddy;
10. выпускает HTTPS-сертификат и проверяет публичный `/api/v1/readiness/`;
11. создаёт persistent volume для системного журнала ошибок;
12. выводит ссылки на ЛК, Admin Console, MFA и диагностику.

Если bootstrap или установка прерваны, повтор той же команды безопасно восстанавливает checkout и продолжает установку без удаления существующих volumes и секретов.

## Полностью non-interactive установка

Для автоматического provisioning можно передать обязательные значения через окружение после того, как репозиторий уже размещён на сервере:

```bash
sudo AIWS_NONINTERACTIVE=true \
  AIWS_DOMAIN=ai.example.ru \
  AIWS_ACME_EMAIL=admin@example.ru \
  AIWS_ADMIN_USERNAME=admin \
  AIWS_ADMIN_EMAIL=admin@example.ru \
  AIWS_ADMIN_PASSWORD='СИЛЬНЫЙ-ПАРОЛЬ-НЕ-МЕНЕЕ-12-СИМВОЛОВ' \
  bash /opt/ai-workspace/install.sh
```

Не храните production-пароль в shell history на рабочем сервере; этот режим предназначен прежде всего для защищённого provisioning/secret manager.

## После установки

Обычная установка поднимает инфраструктуру, но намеренно **не включает коммерческие платежи и реальные AI-провайдеры автоматически**. Перед продажами необходимо заполнить `.env.production` реальными SMTP, AI API, ценами, YooKassa и юридическими реквизитами.

До первого коммерческого `PASS` создайте отдельный обычный пользовательский аккаунт для production smoke-теста: подтвердите его email, оставьте небольшой положительный баланс и не выдавайте ему административные права. В `.env.production` добавьте:

```env
E2E_USERNAME=production-smoke-user
E2E_PASSWORD=отдельный-сильный-пароль
```

`commercial_launch_check.sh` использует этот аккаунт для одного реального AI-запроса через публичный HTTPS API и проверяет полный путь: авторизация → доступная модель → streaming → provider usage → сохранённый ответ → изменение баланса. Без такого аккаунта коммерческий gate намеренно блокирует запуск.

Проверка состояния:

```bash
cd /opt/ai-workspace
sudo bash scripts/system_diagnostics.sh
```

Проверка полной готовности к продажам:

```bash
cd /opt/ai-workspace
sudo bash scripts/commercial_launch_check.sh
```

Commercial gate проверяет код, миграции, frontend build, системные ошибки, production E2E с реальным AI-запросом, платежи, провайдеров, backup/restore/rollback drills и обязательные sign-off. Известная незакрытая системная ошибка блокирует `PASS`.

## Обновление

```bash
cd /opt/ai-workspace
sudo bash scripts/update.sh
```

Перед обновлением создаются backup/evidence и запускаются release gates. Destructive migration блокируется обычным deploy-процессом; такие изменения должны проходить отдельный expand/contract цикл.

## Где менять production-настройки

```bash
sudo nano /opt/ai-workspace/.env.production
sudo docker compose --env-file /opt/ai-workspace/.env.production \
  -f /opt/ai-workspace/docker-compose.prod.yml up -d
```

## Диагностика ошибок

В Admin Console доступен раздел:

```text
/admin-console/system
```

Он показывает:
- состояние AI-запросов;
- проблемных провайдеров;
- сбои платежей;
- backend HTTP 5xx;
- Celery/worker exceptions;
- JavaScript errors авторизованных клиентов;
- correlation ID;
- число повторений;
- первое и последнее появление;
- статус «открыта / разбираемся / исправлена / игнорируется»;
- безопасную трассировку с маскированием распространённых секретов.

Основной реестр багов хранится в PostgreSQL. Дополнительно ведётся ротационный JSONL-журнал в Docker volume `/app/logs` как независимый диагностический канал.

Нельзя публиковать `.env.production`, передавать его в поддержку или копировать в issue/чат.
