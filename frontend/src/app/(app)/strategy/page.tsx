"use client";

import { Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { OpportunityCard } from "@/components/OpportunityCard";
import { api, type Opportunities, type OpportunityStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/format";

const VIEWS = [
  { key: "active", label: "Актуальные" }, { key: "in_factory", label: "В Контент Заводе" }, { key: "done", label: "Опубликованные" },
  { key: "dismissed", label: "Отклонённые" }, { key: "archived", label: "Прошлые подборки" },
] as const;
const RUNNING = new Set(["queued", "running", "analyzing"]);

export default function StrategyPage() {
  const { org } = useAuth();
  const canManage = org?.role !== "viewer";
  const [view, setView] = useState<(typeof VIEWS)[number]["key"]>("active");
  const [data, setData] = useState<Opportunities | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Opportunities>(`/strategy/opportunities?view=${view}`).then(setData).catch((e) => setError(e.message));
  }, [view]);
  useEffect(load, [load]);
  const running = !!data?.job && RUNNING.has(data.job.status);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running, load]);

  async function generate() {
    setError(null);
    try {
      await api("/strategy/opportunities/generate", { method: "POST", json: {} });
      setView("active");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  async function setStatus(id: number, status: OpportunityStatus) {
    try {
      await api(`/strategy/opportunities/${id}`, { method: "PATCH", json: { status } });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  const job = data?.job;
  const message = job?.status === "completed" && typeof job.result?.message === "string" ? job.result.message : null;
  return (
    <>
      <PageHeader title="Стратегия"
                  subtitle="10 тем, о которых стоит писать: где рынок пишет, а вы нет, что растёт и вызывает отклик. Цифры и примеры считает код по вашим конкурентам, формулировки — модель с учётом слабых мест аудита." />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <nav className="flex flex-wrap gap-1">
          {VIEWS.map((v) => (
            <button key={v.key} onClick={() => setView(v.key)}
                    className={`rounded-xl px-3 py-1.5 text-sm ${view === v.key ? "bg-accent-soft font-semibold text-accent" : "hover:bg-surface"}`}>
              {v.label}
            </button>
          ))}
        </nav>
        {canManage && (
          <button className="btn ml-auto" disabled={running} onClick={generate}>
            {running ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            {running ? `Подбираем темы… ${job?.progress ?? 0}%` : "Подобрать 10 тем"}
          </button>
        )}
      </div>
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      {job?.status === "failed" && <p className="mb-3 text-sm text-bad">{job.error}</p>}
      {message && <p className="mb-3 text-sm text-warn">{message}</p>}
      {job?.finished_at && view === "active" && <p className="mb-3 text-xs text-muted">Подборка от {fmtDate(job.finished_at)}</p>}

      {data === null ? <p className="text-sm text-muted">Загрузка…</p> : data.items.length === 0 ? (
        <div className="card text-sm text-muted">
          {view === "active" ? (
            <>Тем пока нет. Нужны размеченные публикации конкурентов и рынка — добавьте их в{" "}
              <Link href="/competitors" className="text-accent">«Конкурентах»</Link> и{" "}
              <Link href="/settings/sources" className="text-accent">«Источниках»</Link>, затем нажмите «Подобрать 10 тем».
              После <Link href="/audit" className="text-accent">аудита</Link> темы подбираются сами.</>
          ) : "Здесь пусто."}
        </div>
      ) : (
        <div className="space-y-3">
          {data.items.map((o) => (
            <OpportunityCard key={o.id} o={o} onStatus={canManage && o.status !== "archived" ? (s) => setStatus(o.id, s) : undefined} />
          ))}
        </div>
      )}
    </>
  );
}
