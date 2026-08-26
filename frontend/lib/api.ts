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
    // Django rotates the CSRF secret on authentication state changes.
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

export async function streamMessage(
  conversationId: string,
  payload: {content: string; client_message_id: string},
  idempotencyKey: string,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
) {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages/stream/`, {
    method: "POST",
    credentials: "include",
    signal,
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
      "X-CSRFToken": await ensureCsrf(),
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
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
      try {
        onEvent({event, data: JSON.parse(data) as Record<string, unknown>});
      } catch {
        // Ignore malformed SSE payloads instead of aborting the stream loop.
      }
    }
    if (done) break;
  }
}
