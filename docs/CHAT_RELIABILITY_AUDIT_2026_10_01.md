# Аудит стабильности AI-чата — 2026-10-01

Исходный коммит: `ea9222052bf977839ec2e08189c0cf99f7ae4e50`.
Ветка исправлений: `fix/chat-customer-reliability`.

## Исправления

| Проблема | Исправление |
|---|---|
| Фронтенд не собирался из-за отсутствующих типов | Восстановлены типы кабинета, памяти и генерации изображений |
| Stop переставал прерывать fetch после HTTP-заголовков | Abort привязан до окончания чтения тела, ресурсы и обработчики освобождаются |
| Поток зависал после первого события | Таймаут простоя и переподключение с исходным idempotency key |
| Завершение ожидало закрытия сокета | Терминальное SSE-событие сразу завершает чтение |
| Сохранённый ответ терялся при reconnect | Абсолютные snapshots заменяют текст; async follower отправляет identity и обновления текста |
| Дубликаты сообщений после долгого preflight | Сопоставление по client_message_id и generation_id вместо текста и времени |
| Reconnect мог запустить QUEUED до завершения preflight/подтверждения цены | Под блокировкой сохраняется разрешение customer_stream_authorized; follower ждёт разрешения |
| EOF/отсутствие usage считались успешным бесплатным ответом | DeepSeek/XAI/OpenRouter/GigaChat требуют признак завершения и корректный usage |
| Сохранение здорового ключа отключало весь канал | Переоценка здоровья только при замене credential/UNKNOWN; здоровые соседние ключи сохраняют доступность |
| Сохранение credential зависело от доступности Celery/Redis | Ограниченная отправка задачи без ожидания result backend и повторов |
| Cancel сохранял следующую непереданную delta | Проверка отмены перед чтением и перед добавлением текста; устойчивое cancelled-событие |
| Недоступность semantic embedding ломала чат с документами | Пустой корпус не вызывает embedding; при сбое используется разрешённый lexical retrieval |
| Preview скрывал spend-limit как отсутствие моделей | Preview возвращает предусмотренный spend guard; реальная отправка сохраняет проверку лимита |
| Проверка миграций обнаруживала дрейф индексов | Имена индексов приведены к уже существующим миграциям |

## Проверки

- Целевой набор backend: **79 passed**, SQLite, Django 5.2.17; отмена, резервирование/usage, reconnect, ASGI/backpressure, подтверждение цены, ключи и RAG fallback.
- Frontend: **8 passed**, реальные ReadableStream в мокированном транспорте; Stop, idle reconnect, snapshots, CRLF, завершение, идентичные запросы.
- Production frontend build: успешно.
- Django check: успешно. `makemigrations --check --dry-run`: No changes detected.
- Ruff для изменённого runtime/новых regression tests, git diff --check, bash -n: успешно.
- Сравнение с исходным коммитом в той же среде: **202 passed, 37 failed** до исправлений; **217 passed, 27 failed** после. Новых падающих test-case IDs в этом прогоне нет; добавлены 5 regression cases.
- Полный набор `apps/chat` (кроме `test_context.py`, `test_web_context.py`): **217 passed, 27 failed**. Эти исключённые модули не проверены в этом прогоне. Отдельный целевой набор не заменяет общий gate.

## Что препятствует заключению о production-готовности

Общий набор не зелёный. Среди оставшихся результатов есть устаревшие ожидания (например, полный refund при подтверждённом usage и получение delta первым событием), неполные фикстуры финансирования и тесты установщиков, меняющие глобальные runtime bindings. Есть и требующие отдельного разбора проверки fallback, execution fence и поисковой политики. Падения не объявлены автоматически ни безвредными, ни доказанными production-сбоями.

PostgreSQL/pgvector, межпроцессные гонки с Redis, production reverse proxy, реальные ключи LLM и production-настройки в этой среде не проверены. SQLite не доказывает корректность row locks и многопроцессного settlement. До rollout нужен зелёный gate на PostgreSQL/Redis и smoke-сценарии с рабочими ключами: успешный ответ/списание, Stop, обрыв/перезагрузка, повтор того же ключа, исчерпание API-баланса, fallback и подтверждение изменения цены. Финансовые проверки должны сверять wallet, reservation, ledger и RequestCost после каждого сценария.

Абсолютная доступность внешних LLM недостижима. Проверяемый пользовательский контракт: один durable запрос, отсутствие повторного списания, ограниченное ожидание, сохранение ответа и правдивый конечный статус.

## Остаточные падения общего набора

```text
FAILED apps/chat/test_manual_llm_system_selection.py::test_customer_can_select_branded_llm_system_model_manually
FAILED apps/chat/test_provider_exhaustion_failover.py::test_customer_gets_llm_system_answer_when_selected_chatgpt_has_no_credits
FAILED apps/chat/test_provider_exhaustion_failover.py::test_commercially_unavailable_primary_is_skipped_before_provider_call
FAILED apps/chat/test_quarantine_race_failover.py::test_model_quarantined_after_prepare_falls_back_without_provider_outage
FAILED apps/chat/test_streaming.py::test_partial_provider_failure_releases_full_reserve
FAILED apps/chat/test_streaming.py::test_client_disconnect_cancels_generation_and_releases_reserve
FAILED apps/chat/test_streaming.py::test_usage_above_reserved_maximum_is_not_debited
FAILED apps/chat/test_streaming.py::test_retry_then_fallback_records_attempts
FAILED apps/chat/test_terminal_overrun_recovery.py::test_completed_provider_response_is_not_lost_when_usage_exceeds_reserve
FAILED apps/chat/tests.py::test_save_before_inference_and_release_on_failure
FAILED apps/chat/tests.py::test_duplicate_send_does_not_duplicate_messages_or_charge
FAILED apps/chat/test_activity_stream.py::test_activity_stream_yields_before_preflight
FAILED apps/chat/test_activity_stream.py::test_activity_stream_reports_real_source_count
FAILED apps/chat/test_conversation_snapshot.py::test_prepare_refreshes_conversation_before_entering_runtime_pipeline
FAILED apps/chat/test_cost_preview_public_identity.py::test_internal_provider_is_hidden_even_after_model_slug_rename
FAILED apps/chat/test_execution_fence.py::ChatExecutionFenceTests::test_completed_attempt_requires_atomic_live_running_lease
FAILED apps/chat/test_execution_fence.py::ChatExecutionFenceTests::test_failed_attempt_cannot_overwrite_recovery_revocation
FAILED apps/chat/test_execution_fence.py::ChatExecutionFenceTests::test_live_attempt_finishes_with_single_compare_and_swap
FAILED apps/chat/test_execution_fence.py::ChatExecutionFenceTests::test_stale_recovery_revokes_running_attempt_before_financial_recovery
FAILED apps/chat/test_public_system_identity.py::test_customer_sse_rewrites_internal_gigachat_names
FAILED apps/chat/test_runtime_entrypoint_wiring.py::test_all_chat_entrypoints_use_final_terminal_recovery_runtime
FAILED apps/chat/test_runtime_entrypoint_wiring.py::test_all_prepare_entrypoints_use_terminal_safe_single_flight_runtime
FAILED apps/chat/test_runtime_safety_wiring.py::test_chat_runtime_guards_are_installed
FAILED apps/chat/test_runtime_safety_wiring.py::test_late_readiness_rejects_stale_candidate_without_provider_call
FAILED apps/chat/test_search_policy_hardening.py::SearchPolicyHardeningTests::test_only_explicit_yandex_request_is_yandex_first
FAILED apps/chat/test_search_runtime_wiring.py::test_activity_stream_uses_final_prepare_runtime
FAILED apps/chat/test_web_search_reliability.py::test_current_and_decision_queries_require_web_search
```
