"use client";

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public extra: Record<string, unknown> = {}) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem("drama.token");
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem("drama.token", t);
    else localStorage.removeItem("drama.token");
  } catch {
    /* storage unavailable: session-only auth */
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const t = getToken();
  if (t) headers.authorization = `Bearer ${t}`;
  const r = await fetch(`${API}${path}`, {
    method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = data?.error || {};
    const detail = Array.isArray(data?.detail) ? data.detail.map((d: any) => d.msg).join("; ") : undefined;
    throw new ApiError(r.status, e.code || "http_error", e.message || detail || r.statusText, e);
  }
  return data as T;
}

export function anonId(): string {
  try {
    let v = localStorage.getItem("drama.anon");
    if (!v) {
      v = crypto.randomUUID();
      localStorage.setItem("drama.anon", v);
    }
    return v;
  } catch {
    return "anon-" + Math.random().toString(36).slice(2);
  }
}

export const money = (minor: number, cur = "USD") =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: cur }).format(minor / 100);
