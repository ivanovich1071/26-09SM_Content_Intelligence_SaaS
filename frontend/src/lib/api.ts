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

/** Скачивание файла с API (экспорт): те же токен и организация, что у api(). */
export async function download(path: string, filename: string): Promise<void> {
  const headers = new Headers();
  const token = read(ACCESS);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const org = read(ORG);
  if (org) headers.set("X-Organization-Id", org);
  const r = await fetch(`/api/v1${path}`, { headers });
  if (r.status === 401 && (await refreshTokens())) return download(path, filename);
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => null))?.detail);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
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
export type SourceKind = "telegram" | "website" | "rss" | "instagram" | "youtube" | "vk";
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
  source?: SourceBrief;
};
export type SourceBrief = {
  id: number;
  name: string;
  kind: SourceKind;
  role: SourceRole;
  competitor_id: number | null;
  competitor_name: string | null;
};
export type FeedPage = { items: Post[]; next_cursor: string | null; search_mode: string | null; notice: string | null };
export type Insight = {
  summary: string;
  hook: string;
  pain_point: string;
  audience: string;
  argumentation: string;
  cta: string;
  format_notes: string;
  why_it_worked: string;
  patterns_to_use: string[];
  do_not_copy: string[];
};
export type PostDetail = {
  post: Post;
  source_median_views: number | null;
  source_median_engagement: number | null;
  insight: Insight | null;
  insight_at: string | null;
  similar: Post[];
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
export type Share = { value: string; count: number; share: number };
export type ContentStats = {
  days: number;
  posts: number;
  analyzed: number;
  posts_per_week: number;
  median_views: number | null;
  median_er: number | null;
  formats: Share[];
  topics: Share[];
  content_types: Share[];
  funnel: Share[];
  hooks: Share[];
  ctas: Share[];
  tone: Share[];
  cta_share: number | null;
  case_share: number | null;
  numbers_share: number | null;
  offer_share: number | null;
  lead_magnet_share: number | null;
  weekly: { week: string; posts: number; median_er: number | null }[];
};
export type CompetitorProfile = {
  summary: string;
  positioning: string;
  audience: string;
  main_topics: string[];
  formats: string[];
  tone_of_voice: string;
  posting_frequency: string;
  ctas: string[];
  content_patterns: string[];
  strengths: string[];
  weaknesses: string[];
  opportunities: string[];
};
export type Competitor = {
  id: number;
  name: string;
  website: string | null;
  notes: string | null;
  created_at: string;
  sources: Source[];
  stats: ContentStats;
  profile: { stats: ContentStats; ai: CompetitorProfile } | null;
  profile_at: string | null;
  profile_job: (Job & { finished_at: string | null }) | null;
};
export type Discovery = {
  website: string;
  title: string | null;
  description: string | null;
  feed_url: string | null;
  social_links: { url: string; kind: SourceKind | null; supported: boolean; needs_key: boolean }[];
};
export type TopicRow = {
  topic: string;
  own: number;
  competitor: number;
  market: number;
  market_total: number;
  share_own: number;
  share_market: number;
  gap: number;
  is_gap: boolean;
  trend_pp: number | null;
  prev_market: number;
  saturation_per_week: number;
  competitors: string[];
  median_er: number | null;
  median_engagement: number | null;
  mean_engagement: number | null;
  subtopics?: { id: number; label: string; size: number }[];
};
export type TopicsOverview = {
  days: number;
  own_total: number;
  market_total: number;
  prev_market_total: number;
  topics: TopicRow[];
  cluster_job: (Job & { finished_at: string | null }) | null;
};
export type GapInsight = {
  data: {
    why: string;
    evidence: string[];
    competitors: string[];
    formats: string[];
    how_to_cover: { angle?: string; format?: string; headline?: string }[];
    risks: string;
  };
  days: number;
  created_at: string;
};
export type GapsResponse = {
  days: number;
  own_total: number;
  market_total: number;
  threshold_pp: number;
  gaps: (TopicRow & { insight: GapInsight | null })[];
};
export type Subtopic = {
  id: number; label: string; description: string | null; keywords: string[]; size: number;
  own: number; competitor: number; market: number;
};
export type TopicDetail = Omit<TopicRow, "subtopics"> & {
  totals: { own: number; market: number };
  weekly: { week: string; own: number; competitor: number; market: number }[];
  formats_market: Share[];
  formats_own: Share[];
  content_types: Share[];
  hooks: Share[];
  by_competitor: { name: string; posts: number }[];
  subtopics: Subtopic[];
  related: { topic: string; similarity: number }[];
  top_posts: Post[];
  own_posts: Post[];
  insight: GapInsight | null;
};
export type Website = {
  id: number;
  url: string;
  name: string | null;
  competitor_id: number | null;
  competitor_name: string | null;
  status: string;
  last_error: string | null;
  last_crawled_at: string | null;
  pages_tracked: number;
  pages_total: number;
  page_limit: number;
  changes_30d: number;
  last_job: (Job & { finished_at: string | null }) | null;
  created_at: string;
};
export type WebsitePage = {
  id: number;
  url: string;
  kind: string;
  title: string | null;
  tracked: boolean;
  status_code: number | null;
  last_checked_at: string | null;
  last_changed_at: string | null;
};
export type WebsiteChange = {
  id: number;
  website_id: number;
  website: string;
  competitor_id: number | null;
  competitor_name: string | null;
  page_id: number;
  page_url: string;
  page_kind: string;
  page_title: string | null;
  kind: "changed" | "new_page" | "removed_page";
  detected_at: string;
  summary: string | null;
  category: string | null;
  importance: "high" | "medium" | "low" | null;
  ai: boolean;
  added_count: number;
  removed_count: number;
};
export type WebsiteChangeDetail = WebsiteChange & {
  added: string[];
  removed: string[];
  before_text: string | null;
  after_text: string | null;
};

export type AuditStatus = "queued" | "running" | "completed" | "failed";
export type AuditStage = "queued" | "sources" | "collect" | "classify" | "metrics" | "audit" | "done";
export type AuditBrief = {
  id: number;
  job_id: number | null;
  company: string;
  website: string | null;
  score: number | null;
  status: AuditStatus;
  stage: AuditStage;
  progress: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};
export type AuditEvidence = { fact: string; post_id?: number; url?: string | null; date?: string | null; source?: string };
export type AuditItem = {
  criterion: string;
  name: string;
  weight: number;
  score: number | null;
  explanation: string;
  evidence: AuditEvidence[];
  recommendations: string[];
  ai: boolean;
  locked: boolean;
};
export type AuditChannel = {
  kind: SourceKind;
  url: string;
  title: string | null;
  followers: number | null;
  origin: "input" | "own" | "site";
  error: string | null;
  message: string | null;
  posts: number;
  analyzed: number;
  posts_per_week: number;
  median_views: number | null;
  median_er: number | null;
  days_since_last_post: number | null;
};
export type BenchmarkRow = { key: string; label: string; own: number | null; market: number | null; top: number | null };
export type AuditResult = {
  days?: number;
  ai?: boolean;
  notes?: string[];
  summary?: string | null;
  strengths?: string[];
  problems?: { title: string; detail: string; criterion: string | null }[];
  problems_hidden?: number;
  site?: {
    url: string; title?: string | null; description?: string | null; error: string | null; forms: number;
    contacts: number; social_links: string[]; pages: { url: string; kind: string; title: string | null }[];
  } | null;
  channels?: AuditChannel[];
  own?: { posts: number; analyzed: number; posts_per_week: number; median_er: number | null;
          topics: { value: string; share: number }[]; funnel: { value: string; share: number }[] };
  benchmark?: { enough: boolean | null; message?: string | null; posts: number | null; min_posts: number | null;
                sources?: number; rows?: BenchmarkRow[] };
  gaps?: { topic: string; share_market: number; share_own: number; gap: number; market_posts: number }[];
  gaps_hidden?: number;
  top_posts?: { post_id: number; url: string | null; date: string | null; source: string; text: string;
                er: number | null; overperformance: number | null }[];
};
export type Audit = AuditBrief & {
  inputs: string[];
  use_own_sources: boolean;
  model: string | null;
  result: AuditResult;
  items: AuditItem[];
  locked: boolean;
  token: string | null;
};

export type OpportunityStatus = "new" | "in_factory" | "done" | "dismissed" | "archived";
export type Opportunity = {
  id: number;
  rank: number;
  title: string;
  topic: string | null;
  why: string;
  angle: string;
  formats: string[];
  funnel_stage: string | null;
  target_role: string | null;
  fixes: string[];
  market: {
    share_market: number; share_own: number; gap: number; trend_pp: number | null; median_er: number | null;
    market_median_er: number | null; saturation_per_week: number; market_total: number; own: number; competitors: string[];
  };
  examples: { post_id: number; url: string | null; date: string | null; source: string; competitor: boolean; text: string;
              er: number | null; overperformance: number | null; format: string }[];
  score: number;
  ai: boolean;
  status: OpportunityStatus;
  audit_id: number | null;
  created_at: string;
};
export type Opportunities = { items: Opportunity[]; job: (Job & { finished_at: string | null }) | null };

export type Brand = {
  company: string | null; website: string | null; description: string | null; offer: string | null;
  audience: string | null; differentiators: string[]; proof_points: string[]; cta: string | null; tone: string | null;
  do: string[]; dont: string[]; examples: string[]; source?: string | null; updated_at?: string | null;
};
export type FormatField = { key: string; label: string; min: number; max: number; multiline: boolean };
export type ContentFormat = { key: string; label: string; rules: string; fields: FormatField[] };
export type QACheck = { code: string; level: "ok" | "warn" | "error"; message: string; source: "code" | "ai";
                        post_id?: number; url?: string | null };
export type ContentVersion = {
  id: number; number: number; kind: "write" | "edit" | "manual"; instruction: string | null;
  fields: Record<string, string>; model: string | null; created_at: string;
  qa: { status?: "ok" | "warn" | "error"; checks?: QACheck[]; ai?: boolean; at?: string };
  context: {
    search?: string | null; topic?: string | null;
    opportunity?: { почему: string; угол: string } | null;
    market_posts?: { post_id: number; url: string | null; source: string; competitor: boolean; text: string;
                     overperformance: number | null; date: string | null }[];
    own_posts?: { post_id: number; url: string | null; text: string }[];
    patterns?: Record<string, unknown>;
    audit?: { слабые_места: { критерий: string; балл: number }[] } | null;
  };
};
export type ContentProjectBrief = {
  id: number; title: string; format: string; status: "draft" | "approved" | "published" | "archived";
  opportunity_id: number | null; versions: number; qa: "ok" | "warn" | "error" | null;
  job: (Job & { finished_at: string | null }) | null; created_at: string; updated_at: string;
};
export type ContentProject = ContentProjectBrief & { brief: string | null; items: ContentVersion[] };

export type DigestPost = { post_id: number; url: string | null; date: string | null; source: string; role: string;
                           format: string; topic: string | null; text: string; er: number | null; overperformance: number | null };
export type DigestSiteChange = { site: string; page: string; url: string; kind: string; summary: string | null;
                                 category: string | null; importance: string | null };
export type DigestStats = {
  period: { from: string; to: string; days: number };
  enough: boolean;
  market: { posts: number; prev_posts: number; delta_pct: number | null; sources: number; median_er: number | null;
            prev_median_er: number | null; formats: { value: string; share: number }[] };
  topics: { rising: { topic: string; share_market: number; trend_pp: number; market_total: number }[];
            new: { topic: string; market_total: number; competitors: string[] }[];
            gaps: { topic: string; share_market: number; share_own: number; gap: number }[] };
  competitors: { id: number; name: string; posts: number; prev_posts: number; median_er: number | null;
                 new_formats: string[]; best_post: DigestPost | null; site_changes: DigestSiteChange[] }[];
  other_site_changes: DigestSiteChange[];
  top_posts: DigestPost[];
  outliers: DigestPost[];
  own: { posts: number; prev_posts: number; delta_pct: number | null; median_er: number | null;
         prev_median_er: number | null; best_post: DigestPost | null; audit: { id: number; score: number | null } | null };
  opportunities: { id: number; title: string; why: string; formats: string[] }[];
};
export type DigestSections = {
  headline: string; summary: string; market: string; topics: string; competitors: string; top_posts: string;
  unusual: string; own: string; recommendations: { title: string; why: string }[];
  ideas: { title: string; why: string; format: string }[];
};
export type DigestBrief = { id: number; title: string; headline: string; period_from: string; period_to: string; days: number;
                            trigger: string; ai: boolean; emailed_at: string | null; email_error: string | null; created_at: string };
export type Digest = DigestBrief & { stats: DigestStats; sections: DigestSections; model: string | null };
export type DigestList = { items: DigestBrief[]; job: (Job & { finished_at: string | null }) | null };
export type DigestSchedule = {
  enabled: boolean; period: "weekly" | "monthly" | "custom"; weekday: number; day: number; every_days: number; hour: number;
  send_email: boolean; recipients: string[]; last_run_at: string | null; next_run_at: string | null; email_configured: boolean;
};
