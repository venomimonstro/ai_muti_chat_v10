const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";
const TEST_USER_KEY = "aiws:test-user";
const API_TIMEOUT_MS = 12000;
const STREAM_OPEN_TIMEOUT_MS = 30000;
const STREAM_FIRST_EVENT_TIMEOUT_MS = 20000;
const STREAM_IDLE_TIMEOUT_MS = 30000;
const streamGuards = new WeakMap<Response, () => void>();
const PENDING_MISSING_GRACE_MS = 30000;
const STREAM_RECONNECT_DELAYS_MS = [500, 1000, 1600, 2500, 4000, 6000, 8000];

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public payload?: unknown,
  ) {
    super(message);
  }
}

let csrfToken = "";

function errorText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(errorText).join(" ");
  if (value && typeof value === "object") {
    return Object.values(value).map(errorText).join(" ");
  }
  return "Не удалось выполнить запрос";
}

function publicStreamError(code: unknown, fallback: unknown): string {
  const value = String(code ?? "");
  if (value.includes("quota_exhausted")) return "У AI-провайдера закончился доступный лимит. Запрос сохранён, деньги не списаны. Администратору нужно проверить баланс или квоту провайдера.";
  if (value.includes("authentication_error") || value.includes("permission_denied") || value === "credential_missing") return "AI-подключение требует проверки администратором. Запрос сохранён, деньги не списаны.";
  if (value.includes("model_not_found")) return "Настроенная AI-модель сейчас недоступна у провайдера. Запрос сохранён, деньги не списаны.";
  if (value.includes("request_too_large") || value.includes("validation_error")) return "Провайдер отклонил текущий контекст запроса. Попробуйте новый чат или уменьшите объём вложений. Деньги не списаны.";
  if (value.includes("rate_limited")) return "AI-провайдер временно ограничил частоту запросов. Повторите через несколько секунд — деньги не списаны.";
  if (value.includes("server_error") || value === "timeout" || value.includes("network")) return "AI-провайдер временно не отвечает. Система сохранила запрос; деньги не списаны. Можно повторить через несколько секунд.";
  if (value === "provider_unavailable") return "Сейчас нет доступного AI-маршрута для этого запроса. Запрос сохранён, деньги не списаны.";
  return typeof fallback === "string" && fallback.trim() ? fallback : "Не удалось получить ответ. Запрос сохранён, деньги не списаны.";
}

function testUserId(path: string): string {
  if (typeof window === "undefined" || path.startsWith("/admin/")) return "";
  try {
    const params = new URLSearchParams(window.location.search);
    const requested = params.get("as_user");
    if (requested === "clear") {
      sessionStorage.removeItem(TEST_USER_KEY);
      return "";
    }
    if (requested && /^[0-9a-f-]{36}$/i.test(requested)) {
      sessionStorage.setItem(TEST_USER_KEY, requested);
      return requested;
    }
    return sessionStorage.getItem(TEST_USER_KEY) ?? "";
  } catch {
    return "";
  }
}

function applyTestUserHeader(headers: Headers, path: string) {
  const id = testUserId(path);
  if (id) headers.set("X-Test-User", id);
}

export function setTestUserMode(userId: string) {
  if (typeof window === "undefined") return;
  if (!/^[0-9a-f-]{36}$/i.test(userId)) throw new Error("Некорректный идентификатор тестового пользователя");
  try { sessionStorage.setItem(TEST_USER_KEY, userId); } catch {}
}

export function clearTestUserMode() {
  if (typeof window === "undefined") return;
  try { sessionStorage.removeItem(TEST_USER_KEY); } catch {}
}

function timeoutSignal(parent?: AbortSignal | null, timeoutMs = API_TIMEOUT_MS) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException("API request timed out", "TimeoutError"));
  }, timeoutMs);
  const abortFromParent = () => controller.abort(parent?.reason);
  if (parent) {
    if (parent.aborted) abortFromParent();
    else parent.addEventListener("abort", abortFromParent, {once: true});
  }
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    clearTimeout: () => window.clearTimeout(timer),
    cleanup: () => {
      window.clearTimeout(timer);
      parent?.removeEventListener("abort", abortFromParent);
    },
  };
}

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = API_TIMEOUT_MS, keepAbortForBody = false) {
  const guard = timeoutSignal(init.signal, timeoutMs);
  let held = false;
  try {
    const response = await fetch(input, {...init, signal: guard.signal});
    if (keepAbortForBody && response.body) {
      guard.clearTimeout();
      streamGuards.set(response, guard.cleanup);
      held = true;
    }
    return response;
  } catch (reason) {
    if (guard.timedOut()) throw new ApiError("Сервер слишком долго не отвечает. Попробуйте ещё раз.", 504, {code: "frontend_api_timeout"});
    throw reason;
  } finally {
    if (!held) guard.cleanup();
  }
}

function releaseStreamResponse(response: Response) {
  streamGuards.get(response)?.();
  streamGuards.delete(response);
  void response.body?.cancel().catch(() => undefined);
}

export async function ensureCsrf() {
  if (csrfToken) return csrfToken;
  const response = await fetchWithTimeout(`${API_BASE}/auth/csrf/`, {credentials: "include"});
  if (!response.ok) throw new ApiError("Не удалось установить защищённое соединение", response.status);
  const data = (await response.json()) as {csrf_token: string};
  csrfToken = data.csrf_token;
  return csrfToken;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  applyTestUserHeader(headers, path);
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRFToken", await ensureCsrf());
  }
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });
  if (["/auth/login/", "/auth/register/", "/auth/logout/", "/auth/logout-all/"].includes(path)) {
    csrfToken = "";
  }
  if (!response.ok) {
    if (response.status === 403) csrfToken = "";
    let message = `Ошибка ${response.status}`;
    let payload: unknown = null;
    try {
      payload = await response.json();
      message = errorText(payload);
    } catch {
      // Response without JSON body.
    }
    throw new ApiError(message, response.status, payload);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export type StreamEvent = {event: string; data: Record<string, unknown>};
type StreamPayload = {content: string; client_message_id: string; file_ids?: string[]};
type PendingStream = {
  payload: StreamPayload;
  idempotencyKey: string;
  createdAt: number;
  confirmedCost?: boolean;
  confirmedMaxRub?: string;
};
type PendingStatus = {state:string;active:boolean;terminal:boolean};
type ChatCostPreview = {
  estimated_min_rub: string;
  estimated_max_rub: string;
  confirmation_required: boolean;
  confirmation_threshold_rub: string;
  selected_model: string;
};

type CostConfirmationError = ChatCostPreview & {
  code?: string;
  detail?: string;
};

type ConsumeResult = "completed" | "retry" | "terminal";

const pendingKey = (conversationId: string) => `aiws:pending-stream:${conversationId}`;
const fileIds = (value: StreamPayload) => value.file_ids ?? [];
const samePayload = (left: StreamPayload, right: StreamPayload) =>
  left.content === right.content && JSON.stringify(fileIds(left)) === JSON.stringify(fileIds(right));

function readPending(conversationId: string, payload: StreamPayload): PendingStream | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(pendingKey(conversationId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingStream;
    if (Date.now() - parsed.createdAt > 24 * 60 * 60 * 1000 || !samePayload(parsed.payload, payload)) {
      localStorage.removeItem(pendingKey(conversationId));
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function writePending(conversationId: string, pending: PendingStream) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(pendingKey(conversationId), JSON.stringify(pending));
  } catch {
    // Storage can be unavailable in hardened/private browser modes.
  }
}

function clearPending(conversationId: string) {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(pendingKey(conversationId));
  } catch {
    // Best-effort reliability cache only.
  }
}

async function verifyPendingStream(conversationId:string,pending:PendingStream,signal?:AbortSignal):Promise<PendingStream|null>{
  try{
    const status=await api<PendingStatus>(`/conversations/${conversationId}/messages/status/?idempotency_key=${encodeURIComponent(pending.idempotencyKey)}`,{signal});
    if(status.active)return pending;
    const age=Math.max(0,Date.now()-Number(pending.createdAt||0));
    if(status.terminal||(status.state==="missing"&&age>=PENDING_MISSING_GRACE_MS)){
      clearPending(conversationId);
      return null;
    }
    return pending;
  }catch(reason){
    if(signal?.aborted)throw reason;
    return pending;
  }
}

export function clearPendingStream(conversationId: string) {
  clearPending(conversationId);
}

function formatRub(value: string | number) {
  const amount = Number(value);
  return Number.isFinite(amount)
    ? amount.toLocaleString("ru-RU", {minimumFractionDigits: 2, maximumFractionDigits: 2})
    : String(value);
}

function askCostConfirmation(maximum: string) {
  if (typeof window === "undefined") return false;
  return window.confirm(
    `Максимальная подтверждаемая стоимость этого запроса — ${formatRub(maximum)} ₽.\n\n` +
      "Сервис не запустит модель, если фактический preflight окажется дороже. Продолжить?",
  );
}

function likelyNeedsResearch(value: string) {
  const text = value.toLocaleLowerCase("ru-RU");
  return /(сегодня|сейчас|текущ|актуальн|курс|доллар|евро|рубл|цена|стоим|сколько стоит|билет|авиа|рейс|расписан|новост|погода|время|интернет|источник|найди|проверь|202[4-9]|203\d)/i.test(text);
}

function activity(
  onEvent: (event: StreamEvent) => void,
  step: string,
  state: "running" | "streaming" | "completed" | "warning" | "failed",
  message: string,
  sourceCount?: number,
) {
  onEvent({
    event: "activity",
    data: {
      step,
      state,
      message,
      ...(typeof sourceCount === "number" ? {source_count: sourceCount} : {}),
    },
  });
}

async function previewChatCost(conversationId: string, payload: StreamPayload, signal?: AbortSignal) {
  const request = () => api<ChatCostPreview>(`/conversations/${conversationId}/messages/preview/`, {
    method: "POST",
    signal,
    body: JSON.stringify(payload),
  });
  try {
    return await request();
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 403 && !signal?.aborted) {
      csrfToken = "";
      await ensureCsrf();
      return request();
    }
    throw reason;
  }
}

async function requestStreamCancellation(conversationId: string, idempotencyKey: string) {
  const path = `/conversations/${conversationId}/messages/cancel/`;
  try {
    await api(path, {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: JSON.stringify({idempotency_key: idempotencyKey}),
    });
  } catch {
    // Best effort here. Durable backend cancellation + stale recovery remain the
    // safety nets if the browser disappears before this request lands.
  }
}

function waitForReconnect(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", aborted);
      resolve();
    }, ms);
    const aborted = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", aborted);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", aborted, {once: true});
  });
}

export async function streamMessage(
  conversationId: string,
  payload: StreamPayload,
  idempotencyKey: string,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
  requestConfirmation?: (estimate: ChatCostPreview) => Promise<boolean>,
) {
  const pendingCandidate = readPending(conversationId, payload);
  const restored = pendingCandidate ? await verifyPendingStream(conversationId,pendingCandidate,signal) : null;
  const pending: PendingStream = restored ?? {
    payload,
    idempotencyKey,
    createdAt: Date.now(),
    confirmedCost: false,
  };
  let cancelPromise: Promise<void> | null = null;
  const cancelBackend = () => {
    clearPending(conversationId);
    if (!cancelPromise) {
      cancelPromise = requestStreamCancellation(conversationId, pending.idempotencyKey);
    }
  };
  signal.addEventListener("abort", cancelBackend, {once: true});
  if (signal.aborted) {
    signal.removeEventListener("abort", cancelBackend);
    cancelBackend();
    await cancelPromise;
    throw signal.reason ?? new DOMException("Aborted", "AbortError");
  }

  try {
  if (likelyNeedsResearch(payload.content)) {
    activity(onEvent, "route", "running", "Определяю сложность запроса и готовлю актуальный поиск…");
    onEvent({event: "routing", data: {explanation: "Проверяю актуальные данные и внешние источники…"}});
  } else {
    activity(onEvent, "route", "running", "Определяю сложность запроса и выбираю подходящую модель…");
    onEvent({event: "routing", data: {explanation: "Подготавливаю контекст и выбираю подходящий режим ответа…"}});
  }

  if (!restored) {
    const preview = await previewChatCost(conversationId, payload, signal);
    if (preview.confirmation_required) {
      if (!(requestConfirmation ? await requestConfirmation(preview) : askCostConfirmation(preview.estimated_max_rub))) {
        throw new ApiError("Запрос отменён до списания средств", 499);
      }
      pending.confirmedCost = true;
      pending.confirmedMaxRub = preview.estimated_max_rub;
    }
  }
  writePending(conversationId, pending);

  const send = (confirmCost: boolean) => {
    const path = `/conversations/${conversationId}/messages/stream/`;
    const headers = new Headers({
      "Content-Type": "application/json",
      "Idempotency-Key": pending.idempotencyKey,
      "X-CSRFToken": csrfToken,
    });
    applyTestUserHeader(headers, path);
    return fetchWithTimeout(`${API_BASE}${path}`, {
      method: "POST",
      credentials: "include",
      signal,
      headers,
      body: JSON.stringify({
        ...pending.payload,
        confirm_cost: confirmCost,
        ...(confirmCost && pending.confirmedMaxRub
          ? {confirmed_max_rub: pending.confirmedMaxRub}
          : {}),
      }),
    }, STREAM_OPEN_TIMEOUT_MS, true);
  };

  const openResponse = async () => {
    await ensureCsrf();
    let response = await send(Boolean(pending.confirmedCost));

    if (response.status === 403 && !signal.aborted) {
      releaseStreamResponse(response);
      csrfToken = "";
      await ensureCsrf();
      response = await send(Boolean(pending.confirmedCost));
    }

    for (let confirmations = 0; response.status === 409 && confirmations < 2; confirmations += 1) {
      let details: CostConfirmationError | null = null;
      try {
        details = (await response.json()) as CostConfirmationError;
      } catch {
        details = null;
      } finally {
        releaseStreamResponse(response);
      }
      if (!details || !["cost_confirmation_required", "cost_confirmation_changed"].includes(String(details.code ?? ""))) {
        throw new ApiError(errorText(details), 409, details);
      }
      if (!(requestConfirmation ? await requestConfirmation(details) : askCostConfirmation(details.estimated_max_rub))) {
        clearPending(conversationId);
        throw new ApiError("Запрос отменён до списания средств", 499, details);
      }
      pending.confirmedCost = true;
      pending.confirmedMaxRub = details.estimated_max_rub;
      writePending(conversationId, pending);
      response = await send(true);
    }

    if (response.status === 409) {
      clearPending(conversationId);
      let details: CostConfirmationError | null = null;
      try {
        details = (await response.json()) as CostConfirmationError;
      } catch {
        details = null;
      } finally {
        releaseStreamResponse(response);
      }
      throw new ApiError(
        "Стоимость запроса продолжает изменяться. Деньги не списаны — повторите отправку после обновления маршрута.",
        409,
        details,
      );
    }

    if (!response.ok || !response.body) {
      if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
        clearPending(conversationId);
      }
      let message = `Ошибка ${response.status}`;
      let errorPayload: unknown = null;
      try {
        errorPayload = await response.json();
        message = errorText(errorPayload);
      } catch {
        // Response without JSON body.
      } finally {
        releaseStreamResponse(response);
      }
      throw new ApiError(message, response.status, errorPayload);
    }
    return response;
  };

  const consume = async (response: Response): Promise<ConsumeResult> => {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;
    let inProgress = false;
    let receivedFirstEvent = false;
    let receivedFirstDelta = false;

    const readChunk = async () => {
      let timer: number | null = null;
      const code = receivedFirstEvent ? "stream_idle_timeout" : "stream_first_event_timeout";
      const abort = () => { void reader.cancel(signal.reason).catch(() => undefined); };
      signal.addEventListener("abort", abort, {once: true});
      try {
        if (signal.aborted) throw signal.reason ?? new DOMException("Aborted", "AbortError");
        return await Promise.race([
          reader.read(),
          new Promise<never>((_resolve, reject) => {
            timer = window.setTimeout(() => {
              void reader.cancel(code).catch(()=>undefined);
              reject(new ApiError(
                "Ответ не начал поступать вовремя. Восстанавливаю тот же запрос без повторного списания.",
                504,
                {code},
              ));
            }, receivedFirstEvent ? STREAM_IDLE_TIMEOUT_MS : STREAM_FIRST_EVENT_TIMEOUT_MS);
          }),
        ]);
      } finally {
        if (timer !== null) window.clearTimeout(timer);
        signal.removeEventListener("abort", abort);
      }
    };

    try {
    while (true) {
      const {done, value} = await readChunk();
      if (signal.aborted) throw signal.reason ?? new DOMException("Aborted", "AbortError");
      if (value?.length) receivedFirstEvent = true;
      buffer += decoder.decode(value, {stream: !done});
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        let event = "message";
        let data = "{}";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) data = line.slice(5).trim();
        }
        const parsed = JSON.parse(data) as Record<string, unknown>;
        const completedSnapshot = event === "snapshot" && String(parsed.state ?? "") === "completed";
        if (completedSnapshot || event === "completed" || event === "cancelled") {
          terminal = true;
          clearPending(conversationId);
        } else if (event === "error" && parsed.code === "generation_in_progress") {
          inProgress = true;
          continue;
        } else if (event === "error") {
          terminal = true;
          clearPending(conversationId);
          parsed.message = publicStreamError(parsed.code, parsed.message);
        }
        if (event === "heartbeat") continue;

        if (event === "generation") {
          activity(onEvent, "provider", "running", "Маршрут готов. Подключаю AI-модель…");
        } else if (event === "routing") {
          activity(onEvent, "route", "completed", String(parsed.explanation ?? "Маршрут выбран"));
        } else if (event === "research_progress" && typeof parsed.message === "string") {
          const phase = String(parsed.phase ?? "research");
          if (phase === "sources_ready") {
            activity(onEvent, "search", "completed", parsed.message, typeof parsed.source_count === "number" ? parsed.source_count : undefined);
          } else if (phase === "search_unavailable") {
            activity(onEvent, "search", "warning", parsed.message);
          } else {
            activity(onEvent, "synthesis", "running", parsed.message);
          }
          onEvent({event: "routing", data: {explanation: parsed.message}});
        } else if (event === "web_search" && parsed.status === "completed") {
          const sources = Array.isArray(parsed.sources) ? parsed.sources.length : 0;
          activity(onEvent, "search", "completed", sources > 0 ? `Проверил актуальные источники: ${sources}` : "Проверил внешние данные", sources);
          onEvent({event: "routing", data: {explanation: sources > 0 ? `Проверил источники: ${sources}. Формирую ответ…` : "Проверил внешние данные. Формирую ответ…"}});
        } else if (event === "recovery") {
          activity(
            onEvent,
            "recovery",
            "warning",
            parsed.action === "fallback" ? "Основной маршрут недоступен. Подключаю резервную модель…" : "Повторяю запрос к провайдеру…",
          );
        } else if (event === "delta" && !receivedFirstDelta) {
          receivedFirstDelta = true;
          activity(onEvent, "provider", "completed", "AI-модель отвечает");
          activity(onEvent, "answer", "streaming", "Формирую ответ…");
        } else if (event === "completed" || completedSnapshot) {
          activity(onEvent, "answer", "completed", "Ответ готов");
        } else if (event === "snapshot") {
          activity(onEvent, "answer", "warning", "Восстановлена сохранённая часть ответа…");
        } else if (event === "cancelled") {
          activity(onEvent, "answer", "warning", "Ответ остановлен");
        } else if (event === "error") {
          activity(onEvent, "error", "failed", String(parsed.message ?? "Не удалось получить ответ"));
        }
        onEvent({event, data: event === "generation" ? {...parsed, client_message_id: pending.payload.client_message_id} : parsed});
        if (terminal) return "completed";
      }
      if (done) {
        if (terminal) return "completed";
        if (inProgress) return "retry";
        return "retry";
      }
    }
    } finally {
      void reader.cancel().catch(() => undefined);
      reader.releaseLock();
      releaseStreamResponse(response);
    }
  };

  let lastError: unknown = null;
  for (let attempt = 0; attempt <= STREAM_RECONNECT_DELAYS_MS.length; attempt += 1) {
    if (signal.aborted) {
      if (cancelPromise) await cancelPromise;
      throw signal.reason ?? new DOMException("Aborted", "AbortError");
    }
    try {
      const response = await openResponse();
      const result = await consume(response);
      if (result === "completed" || result === "terminal") return;
      lastError = new ApiError("Поток ответа завершился раньше времени", 503, {code: "stream_interrupted"});
    } catch (reason) {
      if (signal.aborted || (reason instanceof DOMException && reason.name === "AbortError")) {
        if (cancelPromise) await cancelPromise;
        throw reason;
      }
      if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500 && ![408, 429].includes(reason.status)) {
        throw reason;
      }
      lastError = reason;
    }

    if (attempt >= STREAM_RECONNECT_DELAYS_MS.length) break;
    activity(onEvent, "connection", "warning", "Соединение прервалось. Восстанавливаю тот же ответ без повторного списания…");
    onEvent({
      event: "routing",
      data: {explanation: "Соединение прервалось. Восстанавливаю тот же ответ без повторного списания…"},
    });
    await waitForReconnect(STREAM_RECONNECT_DELAYS_MS[attempt], signal);
  }

  throw lastError instanceof Error
    ? lastError
    : new ApiError("Не удалось восстановить соединение с чатом. Запрос сохранён — обновите чат через несколько секунд.", 503);
  } finally {
    signal.removeEventListener("abort", cancelBackend);
  }
}
