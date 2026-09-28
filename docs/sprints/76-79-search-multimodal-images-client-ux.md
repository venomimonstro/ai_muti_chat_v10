# Sprints 76–79 — Search, Multimodal Chat, Images, Client UX

Владелец продукта открыл следующий продуктовый цикл после Sprint 75. Нумерация продолжается; Sprint 74–75 не отменяются и остаются обязательными runtime/release evidence gates.

## Sprint 76 — Search Reliability

**Статус:** DONE / RUNTIME EVIDENCE

Цель: текущие интернет-данные не должны зависеть от одного платного поискового API.

Реализовано:
- SearXNG используется как бесплатный self-hosted search provider по умолчанию.
- Yandex Search API остаётся подключаемым резервным/приоритетным провайдером.
- Порядок провайдеров задаётся `WEB_SEARCH_PROVIDER_ORDER`, default `searx,yandex`.
- Ошибка/timeout/403/5xx одного провайдера автоматически переключает запрос на следующий.
- Если все провайдеры недоступны, пользователю не выдаются выдуманные live-данные.
- Источники сохраняются в generation context и доступны клиентскому UI.
- Добавлена безопасная production-команда `web_search_diagnose` без вывода API keys.
- Добавлены regression tests на provider failover.

Осталось evidence: live запуск `web_search_diagnose` на production-like окружении.

## Sprint 77 — Multimodal Chat / Vision

**Статус:** DONE / RUNTIME EVIDENCE

Цель: изображение является нативным вложением чата, а не отдельным продуктом.

Реализовано:
- PNG/JPEG/WebP используются как нативные vision-вложения существующего FileAsset/chat pipeline.
- Vision-capable routing получает image blocks вместе с вопросом пользователя.
- Невизуальные модели не выбираются для запроса с image input.
- В composer добавлены явные действия анализа изображения и сравнения изображений.
- Вложения owner/project scoped, ограничены количеством/размером и проходят существующие проверки.
- OpenAI Responses adapter уже передаёт изображения как `input_image`; второй vision-backend не создан.
- Frontend image state использует реальный backend `detected_type=png/jpeg/webp`, а не ошибочное ожидание MIME-строки `image/*`.

Осталось evidence: реальный vision smoke на production provider.

## Sprint 78 — OpenAI Image Generate / Edit in Chat

**Статус:** DONE / RUNTIME EVIDENCE

Цель: генерация и редактирование изображений работают из обычного чата через OpenAI API. Локальный image-generation runtime не строится.

Реализовано:
- OpenAI Images используется через существующий `image_studio`; локальная GPU-генерация не добавлена.
- Генерация открывается непосредственно из composer и может быть привязана к текущему conversation.
- Сохранены существующие reservation/settlement, async queue, private storage, price snapshot и history contracts.
- Добавлен OpenAI image edit через `/images/edits` с multipart PNG/JPEG/WebP source.
- Edit source owner-scoped; `source_file_id` и SHA фиксируются в immutable price snapshot и перепроверяются непосредственно перед provider call.
- Provider failure и invalid/tampered source освобождают резерв и не считаются успешным результатом.
- Для production async file processing UI использует bounded readiness polling, а не ошибочно требует READY сразу после upload.
- Добавлены targeted regression tests на success billing, provider failure, IDOR source access, source tamper и OpenAI multipart contract.
- Chat image dialog имеет create/edit modes, cost confirmation, pending/result/error states и mobile bottom-sheet UI.

Осталось evidence: targeted backend tests, frontend build и реальный OpenAI generate/edit smoke на сервере.

## Sprint 79 — Client Cabinet UX / Reliability Polish

**Статус:** DONE / RUNTIME EVIDENCE

Цель: весь клиентский кабинет выглядит и работает как единый premium-продукт, а не набор технических админ-экранов.

Реализовано:
- Общий `ClientAppChrome` усилен: единая active-state система, focus/touch states, overflow protection, mobile safe-area и fixed bottom navigation.
- Settings приведён к общей типографике, карточкам, формам, focus states и мобильным touch targets.
- Connections полностью переведён с inline-стилей на responsive CSS module без изменения security/ACL логики.
- Notifications получил единые unread/read/error/action states и mobile actions.
- Event Autonomy полностью переведён на responsive premium UI: webhook secret, endpoint, enabled/paused и destructive actions имеют явные состояния.
- Schedules полностью переведён на responsive premium UI: cadence/timezone/day controls, action states и run history адаптированы для мобильных экранов.
- Compare и standalone Images переведены на общий card/result/history contract без разрозненного inline presentation.
- Teams и Team Detail получили premium builder/configuration UX, responsive role/action controls и историю запусков.
- Agent Run Detail получил отдельный responsive layout для approvals, экономики, Dev changes, logs, sources и errors.
- Image create/edit dialog унифицирован с клиентским дизайн-языком и безопасными pending/error states.
- Projects, Agent Studio, Dev Studio, Usage, Wallet, Account и Help проверены; существующие зрелые CSS/component contracts сохранены без лишней переписки.
- `/app/legacy` больше не открывает устаревший второй workspace и перенаправляет в текущий `/app`.
- Добавлен targeted verification gate `scripts/product_expansion_check.sh` для Sprint 76–79.

Статический acceptance выполнен:
- Chat, Projects, Images, Agents, Teams, Team Detail, Run Detail, Dev Studio, Compare, Usage, Wallet, Account, Settings, Help, Connections, Notifications, Events и Schedules прошли единый code-level responsive UX audit.
- Основные действия имеют явные visual states; destructive/security actions отделены от обычных.
- Mobile 360–430 px учитывается отдельными layout/touch правилами.
- Старый параллельный workspace route выведен из эксплуатации.

Осталось runtime evidence:
- `scripts/product_expansion_check.sh` должен вернуть `PRODUCT EXPANSION CHECK: PASS`.
- Frontend production build должен завершиться успешно на сервере.
- Нужен visual smoke основных маршрутов на реальном deployment.

## Release policy

Sprint 74–75 runtime evidence остаётся обязательным для коммерческого release claim. Sprint 76–79 добавляют новый продуктовый код, поэтому перед production promotion выполняется один targeted gate нового пакета, затем один полный release gate. Полный suite не запускается после каждого отдельного CSS/UX изменения.
