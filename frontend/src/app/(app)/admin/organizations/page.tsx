"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Pager } from "@/components/AdminUi";
import { PageHeader } from "@/components/ComingSoon";
import { SUB_STATUS, usd, type AdminOrg, type Paged } from "@/lib/admin";
import { api, type Plan } from "@/lib/api";
import { fmtDate } from "@/lib/format";

function Organizations() {
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState(params.get("plan") ?? "");
  const [sort, setSort] = useState("created");
  const [page, setPage] = useState(1);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [data, setData] = useState<Paged<AdminOrg> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    const qs = new URLSearchParams({ q, plan, sort, page: String(page), per_page: "50" });
    api<Paged<AdminOrg>>(`/admin/organizations?${qs}`).then(setData).catch((e) => setError(e.message));
  }, [q, plan, sort, page]);
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);
  useEffect(() => {
    api<Plan[]>("/billing/plans").then(setPlans).catch(() => null);
  }, []);

  return (
    <>
      <PageHeader title="Организации" subtitle="Клиенты, их тарифы и расход. Тариф и индивидуальные лимиты меняются в карточке организации." />
      <div className="card mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-64 flex-1">
          <label className="label" htmlFor="q">Поиск</label>
          <input id="q" className="input" value={q} placeholder="название, slug или email участника"
                 onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <div className="w-40">
          <label className="label" htmlFor="plan">Тариф</label>
          <select id="plan" className="input" value={plan} onChange={(e) => { setPlan(e.target.value); setPage(1); }}>
            <option value="">все</option>
            {plans.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
          </select>
        </div>
        <div className="w-44">
          <label className="label" htmlFor="sort">Сортировка</label>
          <select id="sort" className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="created">новые сверху</option><option value="cost">по расходу AI</option><option value="name">по названию</option>
          </select>
        </div>
      </div>
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      <div className="card overflow-x-auto">
        {data === null ? <p className="text-sm text-muted">Загрузка…</p> : data.items.length === 0 ? <p className="text-sm text-muted">Ничего не найдено.</p> : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr><th className="py-1">Организация</th><th>Тариф</th><th>Участники</th><th>Источники / конкуренты</th>
                <th>AI за месяц</th><th>Аудиты / генерации</th><th>Активность</th><th>Создана</th></tr>
            </thead>
            <tbody>
              {data.items.map((o) => (
                <tr key={o.id} className="border-t border-line align-top">
                  <td className="py-2">
                    <Link href={`/admin/organizations/${o.id}`} className="font-medium hover:text-accent">{o.name}</Link>
                    {o.system && <span className="ml-1 text-xs text-muted">служебная</span>}
                    <p className="text-xs text-muted">{o.owner_email ?? o.slug}</p>
                  </td>
                  <td>
                    <b>{o.plan}</b>{o.has_override && <span title="индивидуальные лимиты" className="text-accent">*</span>}
                    {o.effective_plan !== o.plan && <p className="text-xs text-bad">сейчас {o.effective_plan}</p>}
                    {o.status && o.status !== "active" && <p className="text-xs text-warn">{SUB_STATUS[o.status] ?? o.status}</p>}
                    {o.current_period_end && <p className="text-xs text-muted">до {fmtDate(o.current_period_end)}</p>}
                  </td>
                  <td>{o.members}</td>
                  <td>{o.sources} / {o.competitors}</td>
                  <td>{usd(o.ai_cost_month)}</td>
                  <td>{o.audits_month} / {o.generations_month}</td>
                  <td className="text-xs">{fmtDate(o.last_job_at)}</td>
                  <td className="text-xs">{fmtDate(o.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && <Pager page={page} perPage={data.per_page} total={data.total} onPage={setPage} />}
      </div>
    </>
  );
}

export default function OrganizationsPage() {
  return <Suspense><Organizations /></Suspense>;
}
