export type Json = Record<string, unknown>;

let csrfToken = "";

export function setCsrfToken(value: string) {
  csrfToken = value;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
    headers.set("Idempotency-Key", crypto.randomUUID());
  }
  const response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: "same-origin" });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(String(payload.detail || `HTTP ${response.status}`));
  }
  return response.status === 204 ? ({} as T) : response.json();
}

export async function adminApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") headers.set("X-Gramly-Admin-Request", "1");
  const response = await fetch(`/api/admin/v1${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(String(payload.detail || `HTTP ${response.status}`));
  }
  return response.status === 204 ? ({} as T) : response.json();
}
