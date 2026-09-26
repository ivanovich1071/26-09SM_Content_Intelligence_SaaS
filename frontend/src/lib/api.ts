// HTTP-клиент к FastAPI. Токены в localStorage, активная организация — заголовок X-Organization-Id.
// Никаких коротких таймаутов: долгие AI-операции идут через jobs и опрос статуса.

const ACCESS = "sm.access";
const REFRESH = "sm.refresh";
const ORG = "sm.org";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : (detail as { message?: string })?.message ?? `HTTP ${status}`);
  }
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* приватный режим — живём без сохранения */
  }
}

export const session = {
  hasToken: () => !!read(ACCESS),
  setTokens(t: { access_token: string; refresh_token: string }) {
    write(ACCESS, t.access_token);
    write(REFRESH, t.refresh_token);
  },
  clear() {
    write(ACCESS, null);
    write(REFRESH, null);
    write(ORG, null);
  },
  orgId: () => read(ORG),
  setOrgId: (id: number | null) => write(ORG, id === null ? null : String(id)),
};

async function refreshTokens(): Promise<boolean> {
  const refresh_token = read(REFRESH);
  if (!refresh_token) return false;
  const r = await fetch("/api/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
  if (!r.ok) return false;
  session.setTokens(await r.json());
  return true;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  const token = read(ACCESS);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const org = read(ORG);
  if (org) headers.set("X-Organization-Id", org);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const r = await fetch(`/api/v1${path}`, { ...init, headers, body });
  if (r.status === 401 && retry && (await refreshTokens())) return api<T>(path, init, false);
  if (r.status === 204) return undefined as T;
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, data?.detail ?? data);
  return data as T;
}

export type Role = "owner" | "admin" | "member" | "viewer";
export type OrgBrief = { id: number; name: string; slug: string; role: Role };
export type Me = { id: number; email: string; full_name: string | null; organizations: OrgBrief[] };
export type Tokens = { access_token: string; refresh_token: string };
export type Usage = {
  plan: string;
  period_start: string;
  limits: Record<string, number | null>;
  used: Record<string, number>;
  ai_by_operation: { operation: string; calls: number; cost_usd: number }[];
};
export type Plan = { code: string; name: string; price_month_usd: number | null; limits: Record<string, number | null> };
export type Member = { id: number; user_id: number; email: string; full_name: string | null; role: Role };
export type Job = {
  id: number;
  kind: string;
  status: string;
  progress: number;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
};
