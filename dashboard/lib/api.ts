// API base resolution:
//  • explicit NEXT_PUBLIC_API_URL  → use it (Vercel / custom host)
//  • else dev (`npm run dev`)      → local backend on :8080
//  • else (production export)      → same-origin "" → relative /api/*,
//    served by the Replit FastAPI server alongside the static dashboard.
const _ENV = process.env.NEXT_PUBLIC_API_URL;
const API_BASE =
  _ENV !== undefined && _ENV !== ""
    ? _ENV
    : process.env.NODE_ENV === "development"
    ? "http://localhost:8080"
    : "";

export async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json();
}

async function send(
  method: string,
  path: string,
  body?: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error)
    throw new Error(String(data.error || `${method} ${path} → ${res.status}`));
  return data;
}

export const apiPost = (p: string, b?: Record<string, unknown>) =>
  send("POST", p, b);
export const apiPatch = (p: string, b?: Record<string, unknown>) =>
  send("PATCH", p, b);
export const apiDelete = (p: string, b?: Record<string, unknown>) =>
  send("DELETE", p, b);

// SWR fetcher — used in all "use client" pages
export const fetcher = (url: string) =>
  fetch(`${API_BASE}${url}`, { cache: "no-store" }).then((r) => {
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  });
