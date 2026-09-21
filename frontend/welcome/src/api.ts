export type Json = Record<string, unknown>;

let csrfToken = "";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown, public requestId = "") {
    super(`${typeof detail === "string" ? detail : `HTTP ${status}`}${requestId ? ` Код ошибки: ${requestId}` : ""}`);
  }
}

export function setCsrfToken(value: string) {
  csrfToken = value;
}

function currentCsrfToken(): string {
  // All Mini App windows share the session cookie. Read its paired CSRF cookie
  // on every write, including when another window has signed in more recently.
  const cookie = document.cookie.split(";").map(value => value.trim())
    .find(value => value.startsWith("gramly_hello_csrf="));
  if (cookie) return cookie.slice("gramly_hello_csrf=".length);
  // Compatibility with servers deployed before the paired cookie was added.
  return csrfToken;
}

export function requestId(): string {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === "function") return webCrypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof webCrypto?.getRandomValues === "function") webCrypto.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

async function fetchWithTimeout(url: string, options: RequestInit, timeoutMs = 12_000): Promise<Response> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort("timeout"), timeoutMs);
  const upstream = options.signal;
  const abort = () => controller.abort(upstream?.reason);
  upstream?.addEventListener("abort", abort, { once: true });
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted && !upstream?.aborted) {
      throw new ApiError(408, "Сервер отвечает слишком долго. Проверьте соединение и попробуйте ещё раз.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    upstream?.removeEventListener("abort", abort);
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const mutation = options.method && !["GET", "HEAD", "OPTIONS"].includes(options.method.toUpperCase());
  if (mutation) {
    headers.set("X-CSRF-Token", currentCsrfToken());
    if (!headers.has("Idempotency-Key")) headers.set("Idempotency-Key", requestId());
  }
  const send = () => fetchWithTimeout(
    `/api/v1${path}`,
    { ...options, headers, credentials: "same-origin" },
    path === "/editor/media" && options.method === "POST" ? 120_000 : 12_000,
  );
  let response = await send();
  if (mutation && response.status === 403) {
    const payload = await response.clone().json().catch(() => ({}));
    const freshToken = currentCsrfToken();
    // A sign-in may finish while the request is in flight. Only retry a CSRF
    // rejection with a changed token; preserve both body and idempotency key.
    if (payload.detail === "Invalid CSRF token" && freshToken && freshToken !== headers.get("X-CSRF-Token")
        && !(options.body instanceof ReadableStream)) {
      headers.set("X-CSRF-Token", freshToken);
      response = await send();
    }
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      payload.detail || `HTTP ${response.status}`,
      payload.request_id || response.headers.get("X-Request-ID") || "",
    );
  }
  return response.status === 204 ? ({} as T) : response.json();
}

export async function adminApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") headers.set("X-Gramly-Admin-Request", "1");
  const response = await fetchWithTimeout(`/api/admin/v1${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
  }, path === "/catalog-media" && options.method === "POST" ? 120_000 : 12_000);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(String(payload.detail || `HTTP ${response.status}`));
  }
  return response.status === 204 ? ({} as T) : response.json();
}
