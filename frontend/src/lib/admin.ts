// Типы и подписи админки (EPIC 13). Доступно только суперадминам — остальным API отвечает 404.
import type { Role } from "@/lib/api";

export type Paged<T> = { items: T[]; total: number; page: number; per_page: number };
export type DayCost = { date: string; calls: number; cost_usd: number; errors: number };
export type CostRow = { key: string | number | null; name?: string; calls: number; cost_usd: number; errors: number;
  input_tokens: number; output_tokens: number };

export type AdminOverview = {
  users: { total: number; active: number; new_7d: number; superadmins: number; seen_7d: number };
  organizations: { total: number; new_7d: number; system: number; paying: number; by_plan: Record<string, number> };
  mrr_usd: number;
  unpriced_paying: number;
  ai: { cost_month: number; cost_prev_month: number; calls_month: number; errors_24h: number; calls_24h: number;
    month_start: string; by_day: DayCost[] };
  jobs: { active: number; failed_24h: number; stuck: number; created_24h: number };
  top_orgs: { id: number; name: string; cost_usd: number; calls: number }[];
};

export type AdminOrg = {
  id: number; name: string; slug: string; system: boolean; created_at: string; owner_email: string | null;
  plan: string; effective_plan: string; status: string | null; current_period_end: string | null;
  has_override: boolean; members: number; sources: number; competitors: number; ai_cost_month: number;
  audits_month: number; generations_month: number; last_job_at: string | null;
};

export type AdminSubscription = {
  plan_code: string; status: string | null; current_period_start: string | null; current_period_end: string | null;
  limits_override: Record<string, number | null> | null; note: string | null; effective_plan: string;
  limits: Record<string, number | null>; active: boolean;
};

export type AdminJob = {
  id: number; organization_id: number; organization: string | null; kind: string; status: string; progress: number;
  params: Record<string, unknown>; error: string | null; created_at: string; started_at: string | null;
  finished_at: string | null; retryable: boolean;
};

export type AdminAction = {
  id: number; at: string; admin: string | null; action: string; organization_id: number | null;
  organization: string | null; target_type: string; target_id: number; details: Record<string, unknown>;
};

export type AdminOrgDetail = {
  id: number; name: string; slug: string; system: boolean; created_at: string;
  subscription: AdminSubscription; used: Record<string, number>;
  counts: { members: number; sources: number; competitors: number; websites: number };
  members: { id: number; user_id: number; email: string; full_name: string | null; role: Role; is_active: boolean;
    is_superadmin: boolean; last_seen_at: string | null }[];
  jobs: AdminJob[]; ai_by_operation: CostRow[]; ai_by_model: CostRow[]; ai_by_day: DayCost[];
  llm_errors: { at: string; operation: string; model: string; error: string }[];
  actions: AdminAction[];
};

export type AdminUser = {
  id: number; email: string; full_name: string | null; is_active: boolean; is_superadmin: boolean;
  created_at: string; last_seen_at: string | null; organizations: { id: number; name: string; role: Role }[];
};

export type AdminCosts = {
  since: string; days: number;
  totals: { calls: number; errors: number; cost_usd: number; input_tokens: number; output_tokens: number;
    avg_latency_ms: number | null };
  by_day: DayCost[]; by_org: CostRow[]; by_model: CostRow[]; by_operation: CostRow[]; by_task: CostRow[];
};

export type AdminJobs = Paged<AdminJob> & { kinds: string[]; by_status_24h: Record<string, number> };

export type AdminError = {
  at: string | null; type: "job" | "llm" | "source" | "website" | "email"; organization_id: number | null;
  organization: string | null; title: string; message: string;
  ref: { job_id?: number | null; retryable?: boolean; url?: string; digest_id?: number };
};

export type AdminProviders = {
  llm: { provider: string; configured: boolean; base_url: string; models: Record<string, string>;
    embedding: { provider: string; model: string; dim: number };
    health_24h: { provider: string; model: string; calls: number; errors: number; error_rate: number;
      p50_latency_ms: number | null; cost_usd: number; last_at: string | null; last_ok_at: string | null }[] };
  connectors: { kind: string; key_setting: string | null; configured: boolean; sources: number; errors: number;
    unavailable: number; last_synced_at: string | null }[];
  email: { configured: boolean; host: string | null; from: string | null };
  captcha: { configured: boolean };
  infra: { redis: string; queue: boolean; worker: boolean };
};

export const ADMIN_NAV = [
  { href: "/admin", title: "Обзор" },
  { href: "/admin/organizations", title: "Организации" },
  { href: "/admin/users", title: "Пользователи" },
  { href: "/admin/costs", title: "Расходы AI" },
  { href: "/admin/jobs", title: "Задачи" },
  { href: "/admin/errors", title: "Ошибки" },
  { href: "/admin/providers", title: "Провайдеры" },
  { href: "/admin/actions", title: "Журнал" },
];

export const SUB_STATUS: Record<string, string> = { active: "активна", past_due: "просрочена", cancelled: "отменена" };
export const JOB_STATUS: Record<string, string> = {
  queued: "в очереди", running: "выполняется", collecting: "сбор", analyzing: "анализ", generating: "генерация",
  validating: "проверка", completed: "готово", failed: "ошибка", cancelled: "отменена",
};
export const JOB_TONE: Record<string, string> = { completed: "text-good", failed: "text-bad", cancelled: "text-muted" };
export const ERROR_TYPES: Record<string, string> = {
  job: "Задачи", llm: "Модель", source: "Источники", website: "Сайты", email: "Email",
};
export const ACTION_NAMES: Record<string, string> = {
  "subscription.update": "Смена тарифа", "user.update": "Пользователь", "job.retry": "Повтор задачи",
  "job.cancel": "Отмена задачи",
};

export const usd = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : `$${n.toLocaleString("en-US", { maximumFractionDigits: n < 1 ? 4 : 2 })}`;

type SubState = { plan_code: string; status: string; current_period_end: string | null;
  limits_override: Record<string, number | null> | null };

function subText(s: SubState) {
  const parts = [s.plan_code];
  if (s.status !== "active") parts.push(SUB_STATUS[s.status] ?? s.status);
  if (s.current_period_end) parts.push(`до ${new Date(s.current_period_end).toLocaleDateString("ru-RU")}`);
  if (s.limits_override) parts.push(`свои лимиты: ${Object.entries(s.limits_override).map(([k, v]) => `${k}=${v ?? "∞"}`).join(", ")}`);
  return parts.join(", ");
}

/** Человеческое описание записи журнала. */
export function describeAction(a: AdminAction): string {
  const d = a.details as Record<string, unknown>;
  if (a.action === "subscription.update") {
    const after = subText(d.after as SubState);
    return d.before ? `${subText(d.before as SubState)} → ${after}` : after;
  }
  if (a.action === "user.update") {
    const out = [String(d.email ?? "")];
    if ("is_active" in d) out.push(d.is_active ? "разблокирован" : "заблокирован");
    if ("is_superadmin" in d) out.push(d.is_superadmin ? "выданы права суперадмина" : "сняты права суперадмина");
    return out.join(": ");
  }
  if (a.action === "job.retry") return `${d.kind} → новая задача #${d.new_job}`;
  if (a.action === "job.cancel") return String(d.kind ?? "");
  return JSON.stringify(d);
}
