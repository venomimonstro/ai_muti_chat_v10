const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
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

export async function ensureCsrf() {
  if (csrfToken) return csrfToken;
  const response = await fetch(`${API_BASE}/auth/csrf/`, {credentials: "include"});
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
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRFToken", await ensureCsrf());
  }
  const response = await fetch(`${API_BASE}${path}`, {
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
    try {
      message = errorText(await response.json());
    } catch {
      // Response without JSON body.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export type StreamEvent = {event: string; data: Record<string, unknown>};
type StreamPayload = {content: string; client_message_id: string; file_ids?: string[]};
type PendingStream = {payload: StreamPayload; idempotencyKey: string; createdAt: number};

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

export function clearPendingStream(conversationId: string) {
  clearPending(conversationId);
}

export async function streamMessage(
  conversationId: string,
  payload: StreamPayload,
  idempotencyKey: string,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
) {
  const pending = readPending(conversationId, payload) ?? {
    payload,
    idempotencyKey,
    createdAt: Date.now(),
  };
  writePending(conversationId, pending);
  signal.addEventListener("abort", () => clearPending(conversationId), {once: true});

  const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages/stream/`, {
    method: "POST",
    credentials: "include",
    signal,
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": pending.idempotencyKey,
      "X-CSRFToken": await ensureCsrf(),
    },
    body: JSON.stringify(pending.payload),
  });
  if (!response.ok || !response.body) {
    if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
      clearPending(conversationId);
    }
    let message = `Ошибка ${response.status}`;
    try {
      message = errorText(await response.json());
    } catch {
      // Response without JSON body.
    }
    throw new ApiError(message, response.status);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const {done, value} = await reader.read();
    buffer += decoder.decode(value, {stream: !done});
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      let event = "message";
      let data = "{}";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data = line.slice(5).trim();
      }
      const parsed = JSON.parse(data) as Record<string, unknown>;
      if (["snapshot", "completed"].includes(event)) {
        clearPending(conversationId);
      } else if (event === "error" && parsed.code !== "generation_in_progress") {
        clearPending(conversationId);
      }
      onEvent({event, data: parsed});
    }
    if (done) break;
  }
}
