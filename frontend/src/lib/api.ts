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
export type SourceKind = "telegram" | "website" | "rss" | "instagram";
export type SourceRole = "own" | "competitor" | "market";
export type SourceStatus = "new" | "ok" | "error" | "unavailable";
export type Source = {
  id: number;
  kind: SourceKind;
  key: string;
  url: string;
  role: SourceRole;
  name: string | null;
  enabled: boolean;
  title: string | null;
  description: string | null;
  followers: number | null;
  status: SourceStatus;
  last_error: string | null;
  last_synced_at: string | null;
  posts_count: number;
  meta: { feed_url?: string | null; social_links?: string[] };
  last_job: (Job & { finished_at: string | null }) | null;
  last_analysis: (Job & { finished_at: string | null }) | null;
  median_views: number | null;
  analyzed_count: number;
  created_at: string;
};
export type Post = {
  id: number;
  external_id: string;
  url: string | null;
  title: string | null;
  text: string;
  published_at: string | null;
  media_type: string;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  engagement: number | null;
  er: number | null;
  overperformance: number | null;
  duplicate_of_id: number | null;
  has_embedding: boolean;
  analysis: PostAnalysis | null;
};
export type PostAnalysis = {
  content_type: string | null;
  funnel_stage: string | null;
  hook_type: string | null;
  cta_type: string | null;
  proof_type: string | null;
  tone: string | null;
  value_type: string | null;
  topic: string | null;
  target_role: string | null;
  has_case: boolean | null;
  has_numbers: boolean | null;
  has_offer: boolean | null;
  has_lead_magnet: boolean | null;
  summary: string | null;
  error: string | null;
};
export type Taxonomy = {
  topics: string[];
  roles: string[];
  niche: string | null;
  version: number;
  source: string | null;
  is_default: boolean;
  universal: Record<string, string[]>;
  updated_at: string | null;
};
export type TaxonomySuggestion = {
  niche: string;
  topics: string[];
  roles: string[];
  rationale: string;
  based_on: { sources: number; posts: number };
};
export type RoleStats = { posts: number; analyzed: number; median_er: number | null; posts_per_week: number };
export type MarketOverview = {
  days: number;
  by_role: Record<SourceRole, RoleStats>;
  topics: { topic: string; own: number; competitor: number; market: number; total: number }[];
  top_posts: {
    id: number;
    source: string;
    role: SourceRole;
    url: string | null;
    text: string;
    published_at: string | null;
    views: number | null;
    er: number | null;
    overperformance: number;
    topic: string | null;
    summary: string | null;
  }[];
};
