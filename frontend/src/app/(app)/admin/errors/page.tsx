"use client";

import { ExternalLink, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { ERROR_TYPES, type AdminError } from "@/lib/admin";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";

type Errors = { items: AdminError[]; counts: Record<string, number>; days: number };

export default function ErrorsPage() {
  const [days, setDays] = useState(7);
  const [kind, setKind] = useState("");
  const [d, setD] = useState<Errors | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Errors>(`/admin/errors?days=${days}&kind=${kind}`).then(setD).catch((e) => setError(e.message));
  }, [days, kind]);
  useEffect(load, [load]);

  async function retry(id: number) {
    setError(null);
    try {
      await api(`/admin/jobs/${id}/retry`, { method: "POST" });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  return (
    <>
      <PageHeader title="Ошибки" subtitle="Упавшие задачи, ошибки модели и письма — за период; источники и сайты — те, что сейчас в ошибке." />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {[1, 7, 30].map((n) => <button key={n} className={n === days ? "btn" : "btn-ghost"} onClick={() => setDays(n)}>{n === 1 ? "24 часа" : `${n} дней`}</button>)}
        <span className="mx-2 h-6 w-px bg-line" />
        <button className={kind === "" ? "btn" : "btn-ghost"} onClick={() => setKind("")}>Все</button>
        {Object.entries(ERROR_TYPES).map(([k, v]) => (
          <button key={k} className={kind === k ? "btn" : "btn-ghost"} onClick={() => setKind(k)}>
            {v}{d && kind === "" && d.counts[k] ? ` ${d.counts[k]}` : ""}
          </button>
        ))}
      </div>
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      <div className="card">
        {d === null ? <p className="text-sm text-muted">Загрузка…</p> : d.items.length === 0 ? <p className="text-sm text-good">Ошибок нет.</p> : (
          <ul className="divide-y divide-line">
            {d.items.map((e, i) => (
              <li key={i} className="flex items-start gap-3 py-2 text-sm">
                <span className="w-24 shrink-0 text-xs text-muted">{ERROR_TYPES[e.type]}<br />{fmtDate(e.at)}</span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{e.title}
                    {e.organization && (e.organization_id
                      ? <Link href={`/admin/organizations/${e.organization_id}`} className="ml-2 text-xs font-normal text-accent">{e.organization}</Link>
                      : <span className="ml-2 text-xs font-normal text-muted">{e.organization}</span>)}
                  </p>
                  <p className="break-words text-muted">{e.message.slice(0, 600)}</p>
                </div>
                {e.ref.url && <a href={e.ref.url} target="_blank" rel="noreferrer" className="btn-ghost"><ExternalLink className="size-4" /></a>}
                {e.type === "job" && e.ref.retryable && e.ref.job_id && (
                  <button className="btn-ghost" title="Повторить" onClick={() => retry(e.ref.job_id!)}><RotateCcw className="size-4" /></button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
