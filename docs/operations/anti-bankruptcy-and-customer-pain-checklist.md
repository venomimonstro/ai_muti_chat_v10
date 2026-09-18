# Anti-bankruptcy и customer-pain checklist

Этот документ фиксирует негативные сценарии, которые могут повредить экономике сервиса, безопасности данных или доверию клиента. Он используется вместе с `commercial_launch_check.sh` и не заменяет реальные production drills.

## P0 — экономика

Перед публикацией обязательно должны выполняться все условия:

- пользователь не может уйти в отрицательный баланс;
- `available_rub = paid_rub + promo_rub` защищено БД constraint;
- каждый платный AI-запрос резервирует worst-case стоимость до вызова провайдера;
- settlement никогда не может списать больше reservation;
- параллельные запросы одного пользователя не могут зарезервировать больше доступного баланса;
- B2B API key обязан иметь собственный или организационный spend limit;
- failed chat/image/compare/B2B request не оставляет активный reservation;
- successful payment не может оставаться без credit;
- cost anomaly / отрицательная маржа создаёт критическую аномалию и отключает проблемного провайдера;
- если фактический provider usage выходит за pre-authorized maximum, пользователь не уходит в кредит, запрос fail-closed, а provider circuit переводится в аварийное состояние;
- stale reservations и stale B2B usages контролируются management checks;
- реальные цены провайдера и FX версионируются, а не перезаписываются задним числом.

Команды контроля:

```bash
python manage.py economic_safety_check
python manage.py verify_financial_invariants
python manage.py customer_trust_check
```

## P0 — безопасность и утечка данных

- session key никогда не передаётся frontend;
- reset password отзывает существующие сессии;
- MFA доступна пользователю и обязательна для platform admin при production policy;
- файлы, изображения, проекты, чаты и GitHub bindings проверяют owner/ACL;
- приватное изображение нельзя получить пользователю другого аккаунта;
- поиск не должен возвращать чужие сообщения, проекты или файлы;
- пользовательские GitHub PAT/OAuth access tokens не сохраняются в БД;
- GitHub App installation token создаётся краткоживущим и ограничивается выбранным repository ID;
- GitHub write выключен по умолчанию;
- запись в GitHub требует отдельного `confirm_write` и актуального SHA файла;
- чтение/запись `.env`, private keys и `.github/workflows` запрещены production policy;
- path traversal (`../`, пустые сегменты, backslash traversal) блокируется;
- API keys, Bearer tokens, пароли и credentials редактируются из system issue logs;
- пользовательский контент не пишется в diagnostic log без необходимости.

## P0 — жалобы «деньги списали, результата нет»

Сервис не должен повторять этот класс жалоб конкурентов.

Обязательные правила:

- failed request: `charged_rub = 0`;
- отменённый запрос без доставленного результата: `charged_rub = 0`;
- если пользователь остановил уже частично полученный ответ, допустимо списание только фактически доставленной части по зафиксированной логике partial billing;
- failed image generation: полное освобождение reservation;
- failed Compare: нет зависшего reservation;
- платёж `succeeded`: баланс должен быть зачислен идемпотентно;
- UI показывает ожидаемую стоимость дорогой операции до запуска;
- история расходов показывает рубли, модель и связанную операцию;
- технические токены не используются как единственная пользовательская единица стоимости.

## P0 — скрытые подписки и автосписания

На текущем коммерческом запуске recurring payments не поддерживаются намеренно.

- карта не должна списываться автоматически;
- пополнение — разовая операция;
- `customer_trust_check` возвращает `recurring_payments_supported=false`;
- если в будущем появится подписка, запуск нового механизма требует отдельного спринта: явное согласие, дата следующего списания, напоминание, отмена в один клик, audit trail и refund policy.

## P1 — поддержка

Нельзя оставлять клиента с оплаченным сервисом и молчащей поддержкой.

- обращение имеет категорию;
- оператор отвечает из Admin Console;
- ответ виден клиенту в ЛК;
- создаётся уведомление;
- превышение `SUPPORT_MAX_UNANSWERED_HOURS` блокирует customer trust gate;
- approaching SLA отображается предупреждением.

## P1 — доступность функций после оплаты

Нельзя рекламировать функцию, которая фактически не настроена.

Commercial launch должен блокироваться, если заявленная функция включена флагом, но не имеет рабочего production backend:

- Compare: минимум две коммерчески готовые text-модели;
- Images: минимум одна реально настроенная image-модель;
- Web search: валидный публичный endpoint и live probe;
- B2B API: feature enabled и реальный paid smoke;
- provider health: свежий, а не исторический;
- цена модели: положительная и уже вступившая в силу.

## P1 — зависания и высокая нагрузка

- streaming одного чата не блокирует другой чат;
- Stop освобождает reservation по правилам billing;
- frontend не рендерит весь длинный чат одновременно;
- sidebar получает lightweight summaries;
- сообщения загружаются страницами;
- streaming UI обновляется батчами, а не на каждый token;
- во время streaming длинный Markdown не парсится заново на каждый chunk;
- background tab снижает частоту визуального обновления;
- drafts сохраняются локально и на сервере;
- reconnect повторяет тот же idempotency identity;
- server/provider timeout не превращается в бесконечный loader.

## P1 — изображения

- `Idempotency-Key` обязателен;
- prompt/count/model/size/quality/conversation входят в idempotency validation;
- provider не может вернуть больше изображений, чем заказано: это fail-closed и circuit breaker;
- MIME проверяется по реальному содержимому;
- размер результата ограничен;
- изображение проходит content validation до сохранения;
- приватный source endpoint проверяет owner;
- изображения могут быть связаны с конкретным чатом;
- «Материалы чата» показывает список всех завершённых image generations;
- provider quality failures агрегируются и могут отключить проблемный image provider;
- неуспешная генерация не расходует клиентский баланс.

## P1 — файлы

- размер upload ограничен;
- zip bomb / compression ratio / число archive entries ограничены;
- PDF имеет page limit;
- удалённые файлы не участвуют в reindex/RAG;
- semantic embeddings разных версий не смешиваются;
- prompt-injection риск хранится на уровне chunks;
- vision/file ACL проверяется до передачи провайдеру;
- «Материалы чата» показывает все реально прикреплённые к generation файлы;
- чужой file ID не должен раскрывать существование файла.

## P1 — поиск внутри чата

Для каждого чата пользователь должен иметь быстрый индекс материалов:

- изображения;
- файлы;
- ссылки из сообщений;
- ссылки из web search sources;
- поиск по prompt/model для изображений;
- поиск по имени файла;
- поиск по URL/title.

Удалённый чат и данные другого пользователя не должны быть доступны через этот индекс.

## P1 — GitHub

GitHub используется через GitHub App, а не через постоянный PAT клиента.

Flow:

1. пользователь устанавливает GitHub App;
2. GitHub OAuth user authorization подтверждает владельца installation;
3. сервис сверяет installation через `/user/installations`;
4. пользователь выбирает конкретный repository;
5. project binding создаётся в read-only режиме;
6. write включается отдельно;
7. каждое изменение требует explicit confirmation и expected SHA;
8. operation log хранит действие и commit SHA, но не repository token.

В production GitHub App должна иметь минимальные permissions. `contents: write` выдаётся только если действительно нужна запись.

## Жалобы конкурентов как отрицательные acceptance criteria

В независимых пользовательских отзывах на российские AI-агрегаторы встречаются повторяющиеся претензии:

- непрозрачный или слишком быстрый расход внутренней валюты;
- неожиданные автосписания;
- сложности с отменой/возвратом;
- деньги списаны, а генерация зависла или не завершилась;
- функция или модель исчезла после покупки;
- длительные генерации и зависания;
- медленная или формальная поддержка;
- непонятно, сколько реально стоит отдельный запрос.

Для AI Workspace эти кейсы считаются дефектами коммерческого запуска, а не «ожидаемым поведением».

## Финальная публикация

Публикация разрешена только после реального production запуска:

```bash
bash scripts/release_check.sh
bash scripts/commercial_launch_check.sh
```

И получения:

```text
RELEASE CHECK: PASS
COMMERCIAL LAUNCH: PASS
```

PASS должен подтверждать не только unit tests, но и paid workspace AI E2E, B2B E2E, system health, economic safety, financial invariants, customer trust, backups/drills и compliance evidence.
