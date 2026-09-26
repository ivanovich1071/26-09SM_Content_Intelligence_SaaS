"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { JobsTable, Pager } from "@/components/AdminUi";
import { PageHeader } from "@/components/ComingSoon";
import { JOB_STATUS, type AdminJobs } from "@/lib/admin";
import { api } from "@/lib/api";

function Jobs() {
  const params = useSearchParams();
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AdminJobs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const orgId = params.get("org_id") ?? "";

  const load = useCallback(() => {
    const qs = new URLSearchParams({ status, kind, page: String(page), per_page: "50" });
    if (orgId) qs.set("org_id", orgId);
    api<AdminJobs>(`/admin/jobs?${qs}`).then(setData).catch((e) => setError(e.message));
  }, [status, kind, page, orgId]);
  useEffect(load, [load]);

  return (
    <>
      <PageHeader title="Задачи" subtitle="Фоновые задачи всех организаций. Повторить можно безопасные задачи (сбор, разметка, профиль, кластеры, обход сайта, стратегия, дайджест); аудит и генерацию пользователь перезапускает сам." />
      <div className="card mb-4 flex flex-wrap items-end gap-3">
        <div className="w-48">
          <label className="label" htmlFor="st">Статус</label>
          <select id="st" className="input" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">все</option><option value="active">в работе</option><option value="stuck">зависли в очереди</option>
            {["failed", "completed", "cancelled", "queued"].map((s) => <option key={s} value={s}>{JOB_STATUS[s]}</option>)}
          </select>
        </div>
        <div className="w-48">
          <label className="label" htmlFor="kind">Тип</label>
          <select id="kind" className="input" value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>
            <option value="">все</option>
            {data?.kinds.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
        {data && (
          <p className="text-xs text-muted">За 24 ч: {Object.entries(data.by_status_24h).map(([k, v]) => `${JOB_STATUS[k] ?? k} ${v}`).join(" · ") || "нет задач"}</p>
        )}
      </div>
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      <div className="card overflow-x-auto">
        {data === null ? <p className="text-sm text-muted">Загрузка…</p> : <JobsTable jobs={data.items} showOrg onChange={load} />}
        {data && <Pager page={page} perPage={data.per_page} total={data.total} onPage={setPage} />}
      </div>
    </>
  );
}

export default function JobsPage() {
  return <Suspense><Jobs /></Suspense>;
}
