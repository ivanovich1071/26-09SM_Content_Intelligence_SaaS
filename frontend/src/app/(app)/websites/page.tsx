"use client";

import { ChevronDown, ChevronRight, Globe, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { PAGE_KINDS, WebsiteChanges } from "@/components/WebsiteChanges";
import { api, type Competitor, type Website, type WebsiteChange, type WebsitePage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/format";

const ACTIVE = new Set(["queued", "running", "collecting", "analyzing"]);

function Pages({ site, canManage, onChange }: { site: Website; canManage: boolean; onChange: () => void }) {
  const [pages, setPages] = useState<WebsitePage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<WebsitePage[]>(`/websites/${site.id}/pages`).then(setPages).catch((e) => setError(e.message));
  }, [site.id]);
  useEffect(load, [load]);

  async function toggle(p: WebsitePage) {
    setError(null);
    try {
      await api(`/websites/${site.id}/pages/${p.id}`, { method: "PATCH", json: { tracked: !p.tracked } });
      load();
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  if (!pages) return <p className="text-sm text-muted">Загрузка…</p>;
  if (pages.length === 0) return <p className="text-sm text-muted">Страницы появятся после первого обхода.</p>;
  return (
    <div>
      {error && <p className="mb-2 text-sm text-bad">{error}</p>}
      <ul className="max-h-80 space-y-1 overflow-y-auto text-sm">
        {pages.map((p) => (
          <li key={p.id} className="flex items-center gap-2">
            <input type="checkbox" checked={p.tracked} disabled={!canManage} onChange={() => toggle(p)} />
            <span className="w-24 shrink-0 text-xs text-muted">{PAGE_KINDS[p.kind] ?? p.kind}</span>
            <a href={p.url} target="_blank" rel="noreferrer" className={`truncate hover:text-accent ${p.tracked ? "" : "text-muted"}`}>
              {p.title || p.url}
            </a>
            {p.last_changed_at && <span className="shrink-0 text-xs text-warn">изм. {fmtDate(p.last_changed_at)}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function WebsitesPage() {
  const { org } = useAuth();
  const canManage = org?.role !== "viewer";
  const [sites, setSites] = useState<Website[] | null>(null);
  const [changes, setChanges] = useState<WebsiteChange[] | null>(null);
  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [importance, setImportance] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [form, setForm] = useState({ url: "", name: "", competitor_id: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Website[]>("/websites").then(setSites).catch((e) => setError(e.message));
    api<WebsiteChange[]>(`/websites/changes?days=90${importance ? `&importance=${importance}` : ""}`)
      .then(setChanges).catch(() => setChanges([]));
  }, [importance]);
  useEffect(load, [load]);
  useEffect(() => {
    api<Competitor[]>("/competitors").then(setCompetitors).catch(() => null);
  }, []);
  const crawling = sites?.some((s) => s.last_job && ACTIVE.has(s.last_job.status)) ?? false;
  useEffect(() => {
    if (!crawling) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [crawling, load]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    await act(async () => {
      await api("/websites", { method: "POST", json: {
        url: form.url, name: form.name || null, competitor_id: form.competitor_id ? Number(form.competitor_id) : null } });
      setForm({ url: "", name: "", competitor_id: "" });
    });
    setBusy(false);
  }

  return (
    <>
      <PageHeader title="Сайты"
                  subtitle="Страницы услуг, цен и блога конкурентов: что изменилось и что это значит. Обход раз в неделю или по кнопке; первый обход только запоминает страницы." />
      {canManage && (
        <form onSubmit={add} className="card mb-4 flex flex-wrap items-end gap-3">
          <div className="min-w-60 flex-1">
            <label className="label" htmlFor="url">Сайт</label>
            <input id="url" required className="input" value={form.url} placeholder="competitor.by"
                   onChange={(e) => setForm({ ...form, url: e.target.value })} />
          </div>
          <div className="w-56">
            <label className="label" htmlFor="sname">Название</label>
            <input id="sname" className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="w-56">
            <label className="label" htmlFor="comp">Конкурент</label>
            <select id="comp" className="input" value={form.competitor_id} onChange={(e) => setForm({ ...form, competitor_id: e.target.value })}>
              <option value="">—</option>
              {competitors.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <button className="btn" disabled={busy}>{busy && <Loader2 className="size-4 animate-spin" />}Отслеживать</button>
        </form>
      )}
      {error && <p className="mb-4 text-sm text-bad">{error}</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="space-y-3">
          {sites === null ? <p className="text-sm text-muted">Загрузка…</p> : sites.length === 0 ? (
            <div className="card text-sm text-muted">Сайтов пока нет. Добавьте сайт конкурента — начнём следить за его страницами.</div>
          ) : sites.map((s) => {
            const job = s.last_job;
            const active = !!job && ACTIVE.has(job.status);
            return (
              <div key={s.id} className="card">
                <div className="flex items-start gap-3">
                  <Globe className="mt-1 size-5 shrink-0 text-accent" />
                  <div className="min-w-0 flex-1">
                    <p className="font-semibold">{s.name || s.competitor_name || s.url}</p>
                    <a href={s.url} target="_blank" rel="noreferrer" className="text-xs text-muted hover:text-accent">{s.url}</a>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
                      <span className={s.status === "ok" ? "text-good" : s.status === "error" ? "text-bad" : "text-muted"}>
                        {s.status === "ok" ? "Работает" : s.status === "error" ? "Ошибка" : "Ожидает обхода"}
                      </span>
                      <span>Страниц: {s.pages_tracked} из {s.page_limit}</span>
                      <span>Изменений за 30 дней: {s.changes_30d}</span>
                      <span className="text-muted">Обход: {fmtDate(s.last_crawled_at)}</span>
                    </div>
                    {s.last_error && s.status === "error" && <p className="mt-1 text-sm text-bad">{s.last_error}</p>}
                    {active && <p className="mt-1 flex items-center gap-1 text-sm text-muted"><Loader2 className="size-3.5 animate-spin" />Обходим… {job.progress}%</p>}
                    {job?.status === "completed" && typeof job.result?.message === "string" && (
                      <p className="mt-1 text-sm text-warn">{job.result.message}</p>
                    )}
                  </div>
                  {canManage && (
                    <div className="flex gap-2">
                      <button className="btn-ghost" disabled={active} title="Обойти сейчас"
                              onClick={() => act(() => api(`/websites/${s.id}/crawl`, { method: "POST" }))}>
                        <RefreshCw className={`size-4 ${active ? "animate-spin" : ""}`} />
                      </button>
                      <button className="btn-ghost text-bad" title="Не отслеживать"
                              onClick={() => confirm(`Перестать отслеживать ${s.url}?`) && act(() => api(`/websites/${s.id}`, { method: "DELETE" }))}>
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                  )}
                </div>
                {s.pages_total > 0 && (
                  <div className="mt-3 border-t border-line pt-3">
                    <button className="flex items-center gap-1 text-sm text-muted hover:text-accent" onClick={() => setOpen(open === s.id ? null : s.id)}>
                      {open === s.id ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                      Страницы ({s.pages_total})
                    </button>
                    {open === s.id && <div className="mt-2"><Pages site={s} canManage={canManage} onChange={load} /></div>}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="card">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-semibold">Изменения за 90 дней</h2>
            <div className="w-44">
              <select className="input" value={importance} onChange={(e) => setImportance(e.target.value)}>
                <option value="">Все</option><option value="high">Важные</option>
                <option value="medium">Заметные</option><option value="low">Мелкие</option>
              </select>
            </div>
          </div>
          {changes === null ? <p className="text-sm text-muted">Загрузка…</p> : <WebsiteChanges changes={changes} />}
        </div>
      </div>
    </>
  );
}
