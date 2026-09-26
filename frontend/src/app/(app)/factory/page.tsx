"use client";

import { Loader2, PenLine } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type ContentProject, type ContentProjectBrief, type Opportunities } from "@/lib/api";
import { FORMAT_NAMES } from "@/lib/audit";
import { useAuth } from "@/lib/auth";
import { QA_NAMES, QA_TONE, RUNNING, STATUS_NAMES } from "@/lib/factory";
import { fmtDate } from "@/lib/format";

export default function FactoryPage() {
  const { org } = useAuth();
  const router = useRouter();
  const canManage = org?.role !== "viewer";
  const [projects, setProjects] = useState<ContentProjectBrief[] | null>(null);
  const [queue, setQueue] = useState<Opportunities["items"]>([]);
  const [view, setView] = useState("active");
  const [form, setForm] = useState({ title: "", brief: "", format: "telegram", opportunity_id: null as number | null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ContentProjectBrief[]>(`/factory/projects?status=${view}`).then(setProjects).catch((e) => setError(e.message));
    api<Opportunities>("/strategy/opportunities?view=in_factory").then((d) => setQueue(d.items)).catch(() => null);
  }, [view]);
  useEffect(load, [load]);
  const running = projects?.some((p) => p.job && RUNNING.has(p.job.status)) ?? false;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [running, load]);

  const taken = new Set(projects?.map((p) => p.opportunity_id).filter(Boolean));
  const waiting = queue.filter((o) => !taken.has(o.id));

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const p = await api<ContentProject>("/factory/projects", { method: "POST", json: { ...form, brief: form.brief || null } });
      router.push(`/factory/${p.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Контент Завод"
                  subtitle="Тема → формат → черновик на контексте рынка и голосе бренда → правки → проверка качества → экспорт. Факты — только из профиля бренда, остальное [шаблоном]." />
      {canManage && (
        <div className="mb-4 grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <form onSubmit={create} className="card space-y-3">
            <h2 className="font-semibold">Новый материал</h2>
            <div>
              <label className="label" htmlFor="title">Тема</label>
              <input id="title" required minLength={3} className="input" value={form.title}
                     onChange={(e) => setForm({ ...form, title: e.target.value, opportunity_id: null })} />
            </div>
            <div>
              <label className="label" htmlFor="brief">Пожелания автору</label>
              <textarea id="brief" rows={2} className="input" value={form.brief} placeholder="Для кого, какой угол, что обязательно упомянуть"
                        onChange={(e) => setForm({ ...form, brief: e.target.value })} />
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-56">
                <label className="label" htmlFor="format">Формат</label>
                <select id="format" className="input" value={form.format} onChange={(e) => setForm({ ...form, format: e.target.value })}>
                  {Object.entries(FORMAT_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              <button className="btn ml-auto" disabled={busy}>{busy ? <Loader2 className="size-4 animate-spin" /> : <PenLine className="size-4" />}Написать черновик</button>
            </div>
            {form.opportunity_id && <p className="text-xs text-accent">Тема из «Стратегии» — контекст рынка подтянется автоматически</p>}
            {error && <p className="text-sm text-bad">{error}</p>}
            <p className="text-xs text-muted">Голос и факты берутся из <Link href="/settings/brand-voice" className="text-accent">Brand Voice</Link>.</p>
          </form>
          <div className="card">
            <h2 className="font-semibold">Темы из «Стратегии»</h2>
            {waiting.length === 0 ? (
              <p className="mt-1 text-sm text-muted">Нет тем в очереди. Отправьте их из <Link href="/strategy" className="text-accent">«Стратегии»</Link>.</p>
            ) : (
              <ul className="mt-2 divide-y divide-line text-sm">
                {waiting.map((o) => (
                  <li key={o.id} className="flex items-center gap-2 py-2">
                    <span className="min-w-0 flex-1">{o.title}</span>
                    <button className="btn-ghost shrink-0" onClick={() => setForm({ title: o.title, brief: o.angle, format: o.formats[0] ?? "telegram", opportunity_id: o.id })}>
                      Взять
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      <div className="card">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">Материалы</h2>
          <div className="w-44">
            <select className="input" value={view} onChange={(e) => setView(e.target.value)}>
              <option value="active">Все в работе</option><option value="draft">Черновики</option>
              <option value="approved">Согласованные</option><option value="published">Опубликованные</option>
              <option value="archived">Архив</option>
            </select>
          </div>
        </div>
        {projects === null ? <p className="text-sm text-muted">Загрузка…</p> : projects.length === 0 ? (
          <p className="text-sm text-muted">Материалов пока нет.</p>
        ) : (
          <ul className="divide-y divide-line">
            {projects.map((p) => {
              const busyJob = p.job && RUNNING.has(p.job.status);
              return (
                <li key={p.id}>
                  <Link href={`/factory/${p.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 hover:text-accent">
                    <span className="min-w-0 flex-1 font-medium">{p.title}</span>
                    <span className="text-xs text-muted">{FORMAT_NAMES[p.format] ?? p.format}</span>
                    <span className="text-xs text-muted">{STATUS_NAMES[p.status]} · версий: {p.versions}</span>
                    {busyJob ? <span className="flex items-center gap-1 text-xs"><Loader2 className="size-3 animate-spin" />пишем…</span> :
                      p.job?.status === "failed" && p.versions === 0 ? <span className="text-xs text-bad">ошибка</span> :
                      p.qa && <span className={`text-xs ${QA_TONE[p.qa]}`}>{QA_NAMES[p.qa]}</span>}
                    <span className="text-xs text-muted">{fmtDate(p.updated_at)}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </>
  );
}
