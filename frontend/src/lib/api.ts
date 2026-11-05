// Thin JSON client. Session cookie + CSRF header for browsers; never holds API keys.
let csrfToken = "";
export function setCsrf(t: string) { csrfToken = t; }

export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any) {
    super(typeof detail === "string" ? detail : detail?.message || `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

type Opts = { method?: string; body?: unknown; form?: FormData; headers?: Record<string, string>; idempotencyKey?: string };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method || (opts.body || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers || {}) };
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, navigator.onLine ? "Couldn't reach the server." : "You're offline. This change wasn't saved.");
  }
  const ctype = res.headers.get("content-type") || "";
  const data = ctype.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/") && !path.startsWith("/setup")) window.dispatchEvent(new Event("larder:unauthorised"));
    throw new ApiError(res.status, (data as any)?.detail ?? data);
  }
  if (res.headers.get("x-larder-offline") === "1") window.dispatchEvent(new Event("larder:stale"));
  return data as T;
}

export const uid = () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36));

export async function waitForJob(id: string, onProgress?: (j: any) => void, timeoutMs = 180000): Promise<any> {
  const start = Date.now();
  let delay = 600;
  for (;;) {
    const j = await api(`/jobs/${id}`);
    onProgress?.(j);
    if (j.status === "succeeded" || j.status === "failed") return j;
    if (Date.now() - start > timeoutMs) throw new ApiError(408, "This is taking longer than expected. Check back in a minute.");
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 1.4, 2500);
  }
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (typeof e.detail === "string") return e.detail;
    if (e.detail?.message) return e.detail.message;
    if (Array.isArray(e.detail)) return e.detail.map((d: any) => d.msg).join("; ");
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
