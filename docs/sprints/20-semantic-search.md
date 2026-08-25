# Sprint 20 — Semantic Search

## Результат

Поиск по Workspace понимает близкие формулировки в истории сообщений и объединяет semantic,
keyword и recency signals. Каждый результат сохраняет точную навигацию до чата и сообщения.

## History embeddings

- `Message.embedding` хранится в pgvector с размерностью 384;
- partial и completed ответы индексируются после сохранения, пользовательский запрос — сразу;
- `content_sha256`, модель индексатора и время индексации обеспечивают lineage;
- миграция backfill индексирует существующую историю и создаёт partial HNSW cosine index;
- `reindex_history` восстанавливает индекс глобально или по user/conversation.

Локальный `local-history-hash-v1` использует слова, биграммы и символьные триграммы. Он не
отправляет историю внешнему API и не создаёт скрытых затрат. Контракт позволяет позже подключить
quality-gated embedding provider без изменения Search API.

## ACL-first hybrid retrieval

Message queryset сначала ограничивается владельцем разговора и разрешёнными фильтрами. Только
после этого выполняется vector ordering. Итоговая оценка включает:

- vector similarity — 50%;
- lexical overlap и точную фразу — 40%;
- recency — 10%.

Vector signal не заменяет keyword и recency. Порог semantic match, веса и scan limit задаются
через environment. Ответ раскрывает только безопасные score signals, но не сами embeddings.

## Filters и navigation

`GET /api/v1/search/` поддерживает `type`, `project`, `conversation`, `role`, `date_from`,
`date_to` и ограниченный `limit`. Недоступный project отклоняется до retrieval.

Message result содержит `conversation_id`, `message_id` и DOM anchor. Интерфейс открывает чат,
прокручивает историю до сообщения и временно подсвечивает найденный фрагмент. Доступны фильтры
по типу результата, проекту и автору сообщения.

## Следующий этап

Sprint 21: Compare, параллельные генерации, branch model, expected cost и synthesis.
