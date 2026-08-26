# Sprint 22 — Images

## Результат

Добавлен самостоятельный контур генерации изображений с приватной галереей и теми же финансовыми инвариантами, что у чата.

## Реализация

- `ImageModel` хранит провайдера, точный upstream id, adapter type, разрешённые размеры/качество и себестоимость единицы.
- Единый `ImageProviderAdapter` реализован для deterministic Echo и OpenAI Images API (`b64_json`, без загрузки произвольных provider URL).
- `POST /api/v1/images/preview/` рассчитывает ожидаемую цену с FX snapshot, markup hierarchy, operation override `images` и Margin Guard.
- `POST /api/v1/images/generations/` требует `Idempotency-Key`, резервирует верхнюю границу и списывает только стоимость фактически валидированных и сохранённых результатов.
- Ошибка провайдера полностью освобождает резерв; дорогая операция требует явного `confirm_cost`.
- PNG/JPEG/WebP проверяются по magic bytes, ограничены по размеру и сохраняются вне публичной media-раздачи.
- `GET /api/v1/images/generations/` возвращает приватную историю владельца с фильтрами `model` и `state`.
- Source URL выдаёт оригинал только владельцу, с `private` cache policy и `nosniff`.
- Next.js drawer объединяет prompt composer, модель, размер, качество, количество, cost preview, историю и адаптивную галерею.

## Инварианты

1. Один `(owner, Idempotency-Key)` создаёт не более одной генерации и одного списания.
2. Фактическое списание не превышает резерв.
3. Provider URL не используются как источник server-side fetch, поэтому адаптер не открывает SSRF-поверхность.
4. Файл результата нельзя получить без session-auth и совпадения владельца.
5. Price Snapshot фиксирует FX, markup rules, unit cost и параметры операции до обращения к провайдеру.

## Проверки

- успешный preview → generate → history;
- идемпотентный повтор;
- ACL приватного source URL;
- полный release при provider failure;
- Django system/migration checks, полный pytest, Ruff, ESLint и production Next build.
