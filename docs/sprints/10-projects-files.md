# Implementation brief: Sprint 10 — Projects and Files Basic

## Цель

Дать пользователю изолированное рабочее пространство: объединять чаты в проекты, хранить инструкции проекта и безопасно загружать файлы с честным статусом обработки.

## Projects

- CRUD API `/api/v1/projects/`;
- owner/editor/viewer ACL на уровне queryset;
- owner membership создаётся автоматически;
- инструкции проекта хранятся версиями, активная версия не перезаписывает историю;
- удаление через API архивирует проект, восстановление выполняется отдельным action;
- чат можно привязать только к доступному незархивированному проекту;
- cross-user project/chat IDOR закрыт тестами.

## Files

- multipart upload `POST /api/v1/files/` с обязательным `Idempotency-Key`;
- generated storage key не зависит от пользовательского имени файла;
- оригинальное имя хранится только как безопасные metadata;
- размер, расширение, declared MIME и magic bytes проверяются до storage;
- запрещены executable, произвольные ZIP, encrypted archive, path traversal и Office macros;
- установлены лимиты количества archive entries, compression ratio и decompressed size;
- полный текстовый файл проверяется как UTF-8 и на бинарные NUL bytes;
- SHA-256 фиксируется при upload;
- доступ к list/retrieve/download/chunks/delete ограничен project ACL;
- физический объект и derived chunks удаляются, metadata lineage остаётся со статусом `deleted`.

## Extraction

- TXT, MD и CSV нормализуются и разбиваются на chunks;
- DOCX извлекается из безопасно проверенного OOXML;
- XLSX извлекается по листам с source location;
- PNG/JPEG/WebP получают `ready` для будущего vision pipeline;
- PDF сохраняется, но получает `partial/pdf_extractor_unavailable`, пока не подключён изолированный parser;
- каждый запуск создаёт `FileProcessingJob`;
- каждый chunk помечен `untrusted_content=true`, поэтому не может считаться системной инструкцией.

## Статусы

`uploaded → quarantine → parsing → ready|partial|failed`, а удаление проходит через `deleting → deleted`.

## Storage

Код использует стандартный Django Storage API. В локальном Docker Compose файлы находятся в отдельном persistent volume `media_data`; production S3-compatible backend подключается конфигурацией storage без изменения бизнес-логики.

## Настройки

- `FILE_MAX_UPLOAD_BYTES`;
- `FILE_MAX_UNCOMPRESSED_BYTES`;
- `FILE_MAX_ARCHIVE_ENTRIES`;
- `FILE_MAX_COMPRESSION_RATIO`;
- `FILE_MAX_EXTRACTED_CHARS`;
- `FILE_CHUNK_CHARS`;
- `FILE_CHUNK_OVERLAP_CHARS`.

## Ограничения текущего этапа

- embeddings, vector search и RAG не входят в Sprint 10;
- содержимое файлов пока не добавляется в prompt автоматически;
- полноценный malware scanner и sandboxed PDF parser являются production gate следующего security/file этапа.
