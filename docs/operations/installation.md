# Установка AI Workspace на сервер

## Что потребуется

- чистый сервер Ubuntu 22.04/24.04 или актуальный Debian;
- минимум 2 vCPU, 4 ГБ RAM и 30 ГБ SSD для небольшого старта;
- домен с A/AAAA-записью на IP сервера;
- открытые входящие порты 80 и 443;
- исходный код проекта на сервере.

## Установка

```bash
git clone https://github.com/venomimonstro/ai_muti_chat_v10.git ai-workspace
cd ai-workspace
chmod +x install.sh
sudo ./install.sh
```

Мастер спросит домен, email для TLS и данные первого администратора. Если пароль оставить
пустым, будет создан случайный пароль. Секреты сохраняются только в `.env.production` с
правами `0600`; файл исключён из Git.

Установщик выполняет:

1. установку и запуск Docker Compose v2, если он отсутствует;
2. генерацию отдельных секретов Django, PostgreSQL, Redis и B2B API;
3. production-сборку backend и frontend;
4. запуск PostgreSQL и Redis;
5. применение миграций и сбор static-файлов;
6. безопасное создание первого platform administrator;
7. запуск worker, beat, frontend и Caddy;
8. автоматическое получение и продление HTTPS-сертификата;
9. проверку `/api/v1/health/`.

Если установка прервалась, повторный запуск `sudo ./install.sh` продолжит её с теми же
секретами. После успешной установки повторный запуск блокируется.

## Обновление

```bash
sudo ./scripts/update.sh
```

Перед обновлением создаётся PostgreSQL dump и SHA-256 checksum в `backups/`. Затем выполняются
fast-forward обновление `main`, production-сборка, миграции, collectstatic и перезапуск сервисов.
Если каталог не является Git clone, скрипт обновит уже размещённый исходный код без `git pull`.

## Где менять настройки

```bash
sudo nano .env.production
sudo docker compose --env-file .env.production -f docker-compose.prod.yml up -d
```

По умолчанию платежи и реальные AI-провайдеры выключены. После добавления ключей необходимо
выполнить юридические/фискальные проверки и строгий gate:

```bash
sudo docker compose --env-file .env.production -f docker-compose.prod.yml \
  exec backend python manage.py prelaunch_check --strict
```

## Диагностика

```bash
sudo docker compose --env-file .env.production -f docker-compose.prod.yml ps
sudo docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=200
curl -fsS https://ВАШ-ДОМЕН/api/v1/readiness/
```

Нельзя публиковать `.env.production`, передавать его в поддержку или копировать в issue.
