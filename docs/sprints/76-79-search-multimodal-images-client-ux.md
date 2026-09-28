# Sprints 76–79 — Search, Multimodal Chat, Images, Client UX

Владелец продукта открыл следующий продуктовый цикл после Sprint 75. Нумерация продолжается; Sprint 74–75 не отменяются и остаются обязательными runtime/release evidence gates.

## Sprint 76 — Search Reliability

**Статус:** IN PROGRESS

Цель: текущие интернет-данные не должны зависеть от одного платного поискового API.

Acceptance criteria:
- SearXNG используется как бесплатный self-hosted search provider по умолчанию.
- Yandex Search API остаётся подключаемым резервным/приоритетным провайдером.
- Порядок провайдеров задаётся `WEB_SEARCH_PROVIDER_ORDER`, default `searx,yandex`.
- Ошибка/timeout/403/5xx одного провайдера автоматически переключает запрос на следующий.
- Если все провайдеры недоступны, пользователю не выдаются выдуманные live-данные.
- Источники сохраняются в generation context и доступны клиентскому UI.
- Есть безопасная production-команда диагностики без вывода API keys.
- Есть regression tests на provider failover.

## Sprint 77 — Multimodal Chat / Vision

**Статус:** PLANNED

Цель: изображение является нативным вложением чата, а не отдельным продуктом.

Acceptance criteria:
- PNG/JPEG/WebP можно прикрепить из composer с preview и удалить до отправки.
- Vision-capable routing получает image blocks вместе с вопросом пользователя.
- Пользователь может анализировать изображение, извлекать видимый текст, сравнивать изображения и обсуждать их в той же беседе.
- Невизуальные модели не выбираются для запроса с image input.
- Ошибки типа/размера/обработки показываются рядом с вложением и не ломают чат.
- Загруженные изображения owner/project scoped и не доступны другому tenant.
- Есть targeted vision regression tests.

## Sprint 78 — OpenAI Image Generate / Edit in Chat

**Статус:** PLANNED

Цель: генерация и редактирование изображений работают из обычного чата через OpenAI API. Локальный image-generation runtime не строится.

Acceptance criteria:
- OpenAI Images — основной production image provider.
- Пользователь запускает генерацию изображения из composer без перехода в отдельную студию.
- Используется существующий billing/reservation/image storage pipeline.
- Генерация привязывается к текущему conversation.
- Результаты показываются в контексте текущего чата и остаются в истории Images.
- Поддерживается редактирование загруженного изображения текстовой инструкцией через OpenAI API там, где это поддерживает настроенная image model.
- До дорогой операции сохраняется действующий cost confirmation contract.
- Provider failure не списывает стоимость за отсутствующий результат.
- Локальные Stable Diffusion/ComfyUI/другие GPU generation services не добавляются.

## Sprint 79 — Client Cabinet UX / Reliability Polish

**Статус:** PLANNED

Цель: весь клиентский кабинет выглядит и работает как единый premium-продукт, а не набор технических админ-экранов.

Acceptance criteria:
- Chat, Projects, Images, Agents, Dev Studio, Compare, Usage, Wallet, Account, Settings, Help проходят единый responsive UX audit.
- Основное действие каждого экрана очевидно без документации.
- Advanced/system details используют progressive disclosure.
- Нет blocking infinite loaders; network errors имеют recoverable states.
- Нет MutationObserver/DOM hacks для основной product logic.
- Длинные списки/диалоги не создают неограниченный DOM и не замораживают вкладку.
- Мобильный интерфейс 360–430 px сохраняет доступ к primary actions.
- Empty/error/loading/disabled states единообразны.
- Frontend build и targeted route smoke входят в acceptance gate.

## Release policy

Sprint 74–75 runtime evidence остаётся обязательным для коммерческого release claim. Sprint 76–79 добавляют новый продуктовый код, поэтому перед production promotion после их завершения требуется один полный release gate; во время разработки используются targeted tests/builds, чтобы не запускать весь suite после каждого файла.
