"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { CostTable, DayBars, JobsTable } from "@/components/AdminUi";
import { ACTION_NAMES, describeAction, SUB_STATUS, usd, type AdminOrgDetail, type AdminSubscription } from "@/lib/admin";
import { api, type Plan } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { METRIC_LABELS, ROLE_LABELS } from "@/lib/nav";

// Лимит → ключ расхода в usage_events (для «использовано за месяц»)
const USED_KEY: Record<string, string> = {
  audits_month: "audits", generations_month: "generations", ai_cost_usd_month: "ai_cost_usd",
};
const lim = (v: number | null | undefined) => (v === null ? "∞" : v === undefined ? "—" : String(v));

function SubscriptionForm({ orgId, sub, plans, onSaved }: {
  orgId: number; sub: AdminSubscription; plans: Plan[]; onSaved: () => void;
}) {
  const [plan, setPlan] = useState(sub.plan_code);
  const [status, setStatus] = useState(sub.status ?? "active");
  const [until, setUntil] = useState(sub.current_period_end?.slice(0, 10) ?? "");
  const [note, setNote] = useState(sub.note ?? "");
  // пусто — как в тарифе, «∞» — без ограничения, число — свой лимит
  const [over, setOver] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(sub.limits_override ?? {}).map(([k, v]) => [k, v === null ? "∞" : String(v)])));
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const base = plans.find((p) => p.code === plan)?.limits ?? {};

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    const limits_override: Record<string, number | null> = {};
    for (const [k, v] of Object.entries(over)) {
      const t = v.trim();
      if (!t) continue;
      if (t === "∞") limits_override[k] = null;
      else if (!Number.isNaN(Number(t))) limits_override[k] = Number(t);
      else return setMsg({ ok: false, text: `${METRIC_LABELS[k]}: число или ∞` });
    }
    try {
      await api(`/admin/organizations/${orgId}/subscription`, { method: "PUT", json: {
        plan_code: plan, status, current_period_end: until ? `${until}T23:59:59Z` : null,
        limits_override: Object.keys(limits_override).length ? limits_override : null, note: note.trim() || null } });
      setMsg({ ok: true, text: "Сохранено" });
      onSaved();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Ошибка" });
    }
  }

  return (
    <form onSubmit={save} className="card space-y-3">
      <h2 className="font-semibold">Подписка</h2>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <div className="w-40">
          <label className="label" htmlFor="plan">Тариф</label>
          <select id="plan" className="input" value={plan} onChange={(e) => setPlan(e.target.value)}>
            {plans.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
          </select>
        </div>
        <div className="w-40">
          <label className="label" htmlFor="st">Статус</label>
          <select id="st" className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            {Object.entries(SUB_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="w-44">
          <label className="label" htmlFor="until">Действует до</label>
          <input id="until" type="date" className="input" value={until} onChange={(e) => setUntil(e.target.value)} />
        </div>
      </div>
      <p className="text-xs text-muted">Пустая дата — бессрочно. После даты или при статусе «просрочена»/«отменена» действуют лимиты Free.</p>
      <details open={Object.keys(over).length > 0}>
        <summary className="cursor-pointer text-sm font-medium">Индивидуальные лимиты</summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {Object.keys(METRIC_LABELS).map((k) => (
            <label key={k} className="flex items-center justify-between gap-2 text-sm">
              <span className="text-muted">{METRIC_LABELS[k]}</span>
              <input className="input w-24" value={over[k] ?? ""} placeholder={lim(base[k])}
                     onChange={(e) => setOver({ ...over, [k]: e.target.value })} />
            </label>
          ))}
        </div>
        <p className="mt-1 text-xs text-muted">Пусто — как в тарифе (серым), «∞» — без ограничения.</p>
      </details>
      <div>
        <label className="label" htmlFor="note">Заметка</label>
        <textarea id="note" className="input min-h-16" value={note} placeholder="договорённости, номер счёта, контакт"
                  onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="flex items-center gap-3">
        <button className="btn">Сохранить</button>
        {msg && <span className={`text-sm ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</span>}
      </div>
    </form>
  );
}

export default function AdminOrganization() {
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<AdminOrgDetail | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<AdminOrgDetail>(`/admin/organizations/${id}`).then(setD).catch((e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);
  useEffect(() => {
    api<Plan[]>("/billing/plans").then(setPlans).catch(() => null);
  }, []);
  if (!d) return <p className="text-sm text-muted">{error ?? "Загрузка…"}</p>;

  const s = d.subscription;
  const counts: Record<string, number> = { members: d.counts.members, sources: d.counts.sources,
    competitors: d.counts.competitors, websites: d.counts.websites };
  return (
    <>
      <Link href="/admin/organizations" className="mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-accent"><ArrowLeft className="size-4" />Организации</Link>
      <h1 className="text-2xl font-bold">{d.name}{d.system && <span className="ml-2 text-sm font-normal text-muted">служебная</span>}</h1>
      <p className="mb-4 text-sm text-muted">
        {d.slug} · создана {fmtDate(d.created_at)} · действует тариф <b className={s.active ? "text-ink" : "text-bad"}>{s.effective_plan}</b>
        {s.current_period_end && ` до ${fmtDate(s.current_period_end)}`}
      </p>

      <div className="grid gap-4 lg:grid-cols-2">
        {plans.length > 0 && <SubscriptionForm key={d.id} orgId={d.id} sub={s} plans={plans} onSaved={load} />}
        <div className="card">
          <h2 className="font-semibold">Лимиты и использование</h2>
          <table className="mt-2 w-full text-sm">
            <tbody>
              {Object.keys(METRIC_LABELS).map((k) => {
                const used = k in counts ? counts[k] : USED_KEY[k] ? d.used[USED_KEY[k]] ?? 0 : null;
                const limit = s.limits[k];
                const over = used !== null && limit !== null && limit !== undefined && used >= limit;
                return (
                  <tr key={k} className="border-t border-line">
                    <td className="py-1.5 text-muted">{METRIC_LABELS[k]}{s.limits_override && k in s.limits_override && <span className="text-accent">*</span>}</td>
                    <td className={`text-right ${over ? "font-semibold text-bad" : ""}`}>
                      {used === null ? "" : `${k === "ai_cost_usd_month" ? usd(used) : used} / `}{lim(limit)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {s.note && <p className="mt-3 rounded-xl bg-bg p-2 text-sm whitespace-pre-line">{s.note}</p>}
        </div>
      </div>

      <div className="card mt-4 overflow-x-auto">
        <h2 className="font-semibold">Участники</h2>
        <table className="mt-2 w-full text-sm">
          <tbody>
            {d.members.map((m) => (
              <tr key={m.id} className="border-t border-line">
                <td className="py-1.5"><Link href={`/admin/users?q=${encodeURIComponent(m.email)}`} className="hover:text-accent">{m.email}</Link>
                  {m.full_name && <span className="text-muted"> · {m.full_name}</span>}</td>
                <td>{ROLE_LABELS[m.role]}</td>
                <td className={m.is_active ? "text-muted" : "text-bad"}>{m.is_active ? `был ${fmtDate(m.last_seen_at)}` : "заблокирован"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="card"><h2 className="mb-3 font-semibold">Расход модели, 30 дней</h2><DayBars days={d.ai_by_day} /></div>
        <div className="card">
          <h2 className="font-semibold">Последние ошибки модели</h2>
          {d.llm_errors.length === 0 ? <p className="mt-2 text-sm text-muted">Нет.</p> : (
            <ul className="mt-2 space-y-2 text-sm">
              {d.llm_errors.map((e, i) => (
                <li key={i}><p className="text-xs text-muted">{fmtDate(e.at)} · {e.model} · {e.operation}</p><p className="break-words">{e.error}</p></li>
              ))}
            </ul>
          )}
        </div>
        <CostTable title="Операции за месяц" keyName="Операция" rows={d.ai_by_operation} />
        <CostTable title="Модели за месяц" keyName="Модель" rows={d.ai_by_model} />
      </div>

      <div className="card mt-4 overflow-x-auto">
        <h2 className="mb-2 font-semibold">Последние задачи</h2>
        <JobsTable jobs={d.jobs} onChange={load} />
      </div>

      <div className="card mt-4">
        <h2 className="font-semibold">Действия администраторов</h2>
        {d.actions.length === 0 ? <p className="mt-2 text-sm text-muted">Нет.</p> : (
          <ul className="mt-2 space-y-1 text-sm">
            {d.actions.map((a) => (
              <li key={a.id}><span className="text-xs text-muted">{fmtDate(a.at)} · {a.admin}</span> {ACTION_NAMES[a.action] ?? a.action}
                <span className="text-muted"> — {describeAction(a)}</span></li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
