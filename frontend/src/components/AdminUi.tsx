"use client";

import { ChevronLeft, ChevronRight, RotateCcw, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { JOB_STATUS, JOB_TONE, usd, type AdminJob, type CostRow, type DayCost } from "@/lib/admin";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";

export function Kpi({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: string; tone?: string }) {
  return (
    <div className="card">
      <p className="text-xs text-muted">{label}</p>
      <p className={`text-xl font-bold ${tone ?? ""}`}>{value}</p>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

/** Столбики по дням: высота — стоимость, красная метка — были ошибки вызовов. */
export function DayBars({ days }: { days: DayCost[] }) {
  const max = Math.max(...days.map((d) => d.cost_usd), 0.0001);
  return (
    <div>
      <div className="flex h-32 items-end gap-0.5">
        {days.map((d) => (
          <div key={d.date} className="group relative flex h-full flex-1 flex-col justify-end"
               title={`${d.date}: ${usd(d.cost_usd)}, вызовов ${d.calls}${d.errors ? `, ошибок ${d.errors}` : ""}`}>
            <div className={`rounded-t ${d.errors ? "bg-warn" : "bg-accent"}`}
                 style={{ height: `${Math.max(d.calls ? 2 : 0, (d.cost_usd / max) * 100)}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-xs text-muted">
        <span>{days[0]?.date}</span><span>оранжевым — дни с ошибками вызовов</span><span>{days[days.length - 1]?.date}</span>
      </div>
    </div>
  );
}

export function CostTable({ rows, title, keyName }: { rows: CostRow[]; title: string; keyName: string }) {
  const total = rows.reduce((s, r) => s + r.cost_usd, 0) || 1;
  return (
    <div className="card overflow-x-auto">
      <h2 className="font-semibold">{title}</h2>
      {rows.length === 0 ? <p className="mt-2 text-sm text-muted">Вызовов нет.</p> : (
        <table className="mt-2 w-full text-sm [&_td]:pr-3 [&_th]:pr-3 [&_th]:whitespace-nowrap">
          <thead className="text-left text-xs text-muted">
            <tr><th className="py-1">{keyName}</th><th>Вызовов</th><th>Ошибок</th><th>Токены вх/вых</th><th className="text-right">Стоимость</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={String(r.key)} className="border-t border-line">
                <td className="max-w-64 truncate py-1.5">{r.name ?? r.key ?? "—"}</td>
                <td>{r.calls}</td>
                <td className={r.errors ? "text-bad" : "text-muted"}>{r.errors}</td>
                <td className="whitespace-nowrap text-muted">{r.input_tokens.toLocaleString("ru-RU")} / {r.output_tokens.toLocaleString("ru-RU")}</td>
                <td className="text-right whitespace-nowrap">{usd(r.cost_usd)} <span className="text-xs text-muted">{Math.round((r.cost_usd / total) * 100)}%</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function Pager({ page, perPage, total, onPage }: { page: number; perPage: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / perPage));
  if (pages <= 1) return <p className="mt-3 text-xs text-muted">Всего: {total}</p>;
  return (
    <div className="mt-3 flex items-center gap-2 text-sm">
      <button className="btn-ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft className="size-4" /></button>
      <span>{page} / {pages}</span>
      <button className="btn-ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}><ChevronRight className="size-4" /></button>
      <span className="text-xs text-muted">всего {total}</span>
    </div>
  );
}

export function Flag({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return <span className={`text-sm font-medium ${ok ? "text-good" : "text-bad"}`}>{ok ? yes : no}</span>;
}

/** Таблица задач с отменой и повтором (повтор — только для безопасных типов, решает API). */
export function JobsTable({ jobs, showOrg, onChange }: { jobs: AdminJob[]; showOrg?: boolean; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function act(id: number, what: "retry" | "cancel") {
    setError(null);
    try {
      await api(`/admin/jobs/${id}/${what}`, { method: "POST" });
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }
  if (jobs.length === 0) return <p className="text-sm text-muted">Задач нет.</p>;
  return (
    <>
      {error && <p className="mb-2 text-sm text-bad">{error}</p>}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr><th className="py-1">#</th>{showOrg && <th>Организация</th>}<th>Тип</th><th>Статус</th><th>Создана</th><th>Длилась</th><th /></tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id} className="border-t border-line align-top">
              <td className="py-1.5 text-muted">{j.id}</td>
              {showOrg && <td><Link href={`/admin/organizations/${j.organization_id}`} className="hover:text-accent">{j.organization}</Link></td>}
              <td>{j.kind}</td>
              <td className={JOB_TONE[j.status] ?? "text-accent"}>
                {JOB_STATUS[j.status] ?? j.status}{!JOB_TONE[j.status] && j.progress ? ` ${j.progress}%` : ""}
                {j.error && <p className="max-w-md text-xs break-words text-muted">{j.error.slice(0, 300)}</p>}
              </td>
              <td className="text-xs whitespace-nowrap">{fmtDate(j.created_at)}</td>
              <td className="text-xs">{duration(j.started_at, j.finished_at)}</td>
              <td className="text-right whitespace-nowrap">
                {j.retryable && <button className="btn-ghost" title="Повторить" onClick={() => act(j.id, "retry")}><RotateCcw className="size-4" /></button>}
                {!JOB_TONE[j.status] && <button className="btn-ghost" title="Отменить" onClick={() => act(j.id, "cancel")}><X className="size-4" /></button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function duration(start: string | null, end: string | null) {
  if (!start || !end) return "—";
  const s = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000);
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`;
}
