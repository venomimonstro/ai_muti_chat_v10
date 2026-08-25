# Sprint 19 — RAG v1

## Результат

Файлы проекта индексируются в изолированный RAG-контур: paragraph-aware chunks получают
воспроизводимые embeddings, ACL metadata, provenance hash и оценку prompt injection. Retrieval
сначала ограничивает queryset доступным проектом и только затем применяет vector ranking.

## Индекс

- PostgreSQL запускается на `pgvector/pgvector:pg17`;
- миграция включает extension `vector` и создаёт partial HNSW cosine index;
- `FileChunk.embedding` имеет фиксированную размерность 384;
- `local-hash-v1` не отправляет содержимое файлов внешнему провайдеру и не создаёт скрытых затрат;
- `reindex_file_chunks --file UUID` или `--project UUID` обновляет embeddings, ACL и security flags.

SQLite использует тот же model field и локальный cosine fallback, поэтому unit-тесты не требуют
отдельного PostgreSQL. В production PostgreSQL выполняет nearest-neighbor ordering через pgvector.

## ACL до retrieval

Каждый chunk хранит `acl_owner_id` и `acl_project_id`. До vector/lexical ranking проверяются:

1. доступ пользователя к активному проекту;
2. точное совпадение requested project и file project;
3. совпадение denormalized ACL metadata с владельцем файла;
4. ready/partial status и отсутствие soft deletion.

Endpoint `POST /api/v1/files/retrieve/` возвращает только разрешённые snippets, scores и citation.
После удаления файла все chunks и embeddings удаляются каскадно.

## Citations

Каждый результат имеет стабильный ключ `file:<file-id>:chunk:<position>`, file/chunk/project ids,
source location и SHA-256 содержимого. Smart Context сохраняет эти данные в immutable snapshot и
передаёт модели внутри явно обозначенного блока `FILE_DATA`. File-grounded ответы обязаны ссылаться
на идентификатор в квадратных скобках.

## Prompt injection defense

Индексатор распознаёт override system instructions, role impersonation, попытки раскрыть секреты,
вызвать tool/command/network action и инструкции для модели. `blocked` chunks никогда не участвуют
в retrieval; `suspicious` остаются недоверенными данными. System policy запрещает исполнять любые
инструкции из `FILE_DATA`, менять из-за них правила или вызывать инструменты.

## Следующий этап

Sprint 20: semantic search по истории сообщений, общая hybrid search выдача, фильтры и переход к
результату.
