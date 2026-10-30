import type { ApiErrorResponse } from "./types";

export async function api<T = unknown>(
  url: string,
  opts: RequestInit = {},
): Promise<T> {
  const r = await fetch(url, opts);
  if (!r.ok) {
    const body = await r.json().catch(() => null) as ApiErrorResponse | null;
    const detail = typeof body?.detail === "string"
      ? body.detail
      : body?.detail?.[0]?.msg;
    throw new Error(detail || `${r.status} ${r.statusText}`);
  }
  return r.json() as Promise<T>;
}
