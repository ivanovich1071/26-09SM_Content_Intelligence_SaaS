"use client";

import { ChevronDown, ChevronRight, Loader2, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Post, type Source, type SourceRole, type SourceStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { PostList } from "@/components/PostList";
import { fmtDate, fmtNum, KIND_ICON, KIND_LABELS } from "@/lib/format";

const ROLE_LABELS: Record<SourceRole, string> = { own: "Свой", competitor: "Конкурент", market: "Рынок" };
const STATUS: Record<SourceStatus, { label: string; cls: string }> = {
  new: { label: "Ожидает сбора", cls: "text-muted" },
  ok: { label: "Работает", cls: "text-good" },
  error: { label: "Ошибка", cls: "text-bad" },
  unavailable: { label: "Недоступен", cls: "text-warn" },
};
const ACTIVE = new Set(["queued", "running", "collecting", "analyzing"]);


function jobNote(s: Source): { text: string; cls: string } | null {
  const j = s.last_job;
  if (!j) return null;
  if (ACTIVE.has(j.status)) return { text: j.status === "queued" ? "В очереди…" : "Собираем…", cls: "text-muted" };
  if (j.status === "failed") {
    // ошибку прошлой попытки не показываем, если источник уже собран успешно
    return s.status === "ok" ? null : { text: j.error ?? "Сбор не удался", cls: "text-bad" };
  }
  const r = j.result as { skipped?: boolean; posts_new?: number; posts_updated?: number; message?: string } | null;
  if (!r) return null;
  if (r.skipped) return { text: r.message ?? "Данные свежие", cls: "text-muted" };
  const parts = [`новых постов: ${r.posts_new ?? 0}`, `обновлено: ${r.posts_updated ?? 0}`];
  return { text: [parts.join(", "), r.message].filter(Boolean).join(". "), cls: "text-muted" };
}

function analysisNote(s: Source): { text: string; cls: string } | null {
  const j = s.last_analysis;
  if (!j) return null;
  if (ACTIVE.has(j.status)) {
    return { text: j.status === "queued" ? "Анализ в очереди…" : `Анализируем… ${j.progress}%`, cls: "text-muted" };
  }
  if (j.status === "failed") return { text: `Анализ не удался: ${j.error ?? ""}`, cls: "text-bad" };
  const r = j.result as {
    classified?: number; no_text?: number; duplicates?: number; message?: string; classify_stopped?: string;
    embed_error?: string;
  } | null;
  if (!r) return null;
  const warn = r.message ?? r.classify_stopped ?? r.embed_error;
  const parts = [`размечено: ${s.analyzed_count} из ${s.posts_count}`];
  if (r.duplicates) parts.push(`дублей: ${r.duplicates}`);
  return { text: [parts.join(", "), warn].filter(Boolean).join(". "), cls: warn ? "text-warn" : "text-muted" };
}


function Posts({ sourceId }: { sourceId: number }) {
  const [posts, setPosts] = useState<Post[] | null>(null);
  useEffect(() => {
    api<Post[]>(`/sources/${sourceId}/posts?limit=5`).then(setPosts).catch(() => setPosts([]));
  }, [sourceId]);
  if (posts === null) return <p className="text-sm text-muted">Загрузка…</p>;
  return <PostList posts={posts} />;
}

export default function SourcesPage() {
  const { org } = useAuth();
  const canManage = org?.role !== "viewer";
  const [sources, setSources] = useState<Source[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Source[]>("/sources").then(setSources).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const syncing = sources?.some((s) => [s.last_job, s.last_analysis].some((j) => j && ACTIVE.has(j.status))) ?? false;
  useEffect(() => {
    if (!syncing) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [syncing, load]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  async function add(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const kind = String(f.get("kind"));
    setBusy(true);
    await act(async () => {
      await api("/sources", {
        method: "POST",
        json: { url: f.get("url"), role: f.get("role"), kind: kind === "auto" ? null : kind },
      });
      form.reset();
    });
    setBusy(false);
  }

  return (
    <>
      <PageHeader
        title="Источники"
        subtitle="Каналы и сайты, которые собираем: ваши, конкурентов и рынка. Telegram, VK, YouTube и RSS обновляются ежедневно, сайты и Instagram — еженедельно."
      />

      {canManage && (
        <form onSubmit={add} className="card mb-4 flex flex-wrap items-end gap-3">
          <div className="min-w-72 flex-1">
            <label className="label" htmlFor="url">Адрес</label>
            <input id="url" name="url" required className="input" placeholder="t.me/канал, instagram.com/…, youtube.com/@…, vk.com/…, сайт или RSS" />
          </div>
          <div>
            <label className="label" htmlFor="kind">Тип</label>
            <select id="kind" name="kind" className="input w-40" defaultValue="auto">
              <option value="auto">Определить</option>
              {Object.entries(KIND_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="role">Чей</label>
            <select id="role" name="role" className="input w-40" defaultValue="market">
              {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <button className="btn" disabled={busy}>{busy && <Loader2 className="size-4 animate-spin" />}Добавить</button>
        </form>
      )}
      {error && <p className="mb-4 text-sm text-bad">{error}</p>}

      {sources === null ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : sources.length === 0 ? (
        <div className="card text-sm text-muted">
          Источников пока нет. Начните со своего Telegram-канала или сайта — с ними будем сравнивать рынок.
        </div>
      ) : (
        <div className="space-y-3">
          {sources.map((s) => {
            const Icon = KIND_ICON[s.kind];
            const active = !!s.last_job && ACTIVE.has(s.last_job.status);
            const note = jobNote(s);
            const aNote = analysisNote(s);
            const analyzing = !!s.last_analysis && ACTIVE.has(s.last_analysis.status);
            return (
              <div key={s.id} className="card">
                <div className="flex flex-wrap items-start gap-4">
                  <Icon className="mt-1 size-5 shrink-0 text-accent" />
                  <div className="min-w-60 flex-1">
                    <p className="font-semibold">
                      {s.name || s.title || s.key}
                      <span className="ml-2 rounded-lg bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent">{ROLE_LABELS[s.role]}</span>
                    </p>
                    <a href={s.url} target="_blank" rel="noreferrer" className="text-xs text-muted hover:text-accent">{s.url}</a>
                    <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm">
                      <span className={STATUS[s.status].cls}>{STATUS[s.status].label}</span>
                      {s.followers !== null && <span>Подписчики: {fmtNum(s.followers)}</span>}
                      <span>Постов: {fmtNum(s.posts_count)}</span>
                      {s.median_views !== null && <span>Медиана просмотров: {fmtNum(Math.round(s.median_views))}</span>}
                      <span className="text-muted">Обновлён: {fmtDate(s.last_synced_at)}</span>
                    </div>
                    {s.last_error && s.status !== "ok" && <p className="mt-1 text-sm text-bad">{s.last_error}</p>}
                    {note && !(s.last_error && note.cls === "text-bad") && (
                      <p className={`mt-1 flex items-center gap-1 text-sm ${note.cls}`}>
                        {active && <Loader2 className="size-3.5 animate-spin" />}
                        {note.text}
                      </p>
                    )}
                    {aNote && s.status === "ok" && (
                      <p className={`mt-1 flex items-center gap-1 text-sm ${aNote.cls}`}>
                        {analyzing ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
                        {aNote.text}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    {canManage && (
                      <button className="btn-ghost" disabled={active} title="Собрать сейчас"
                              onClick={() => act(() => api(`/sources/${s.id}/sync`, { method: "POST" }))}>
                        <RefreshCw className={`size-4 ${active ? "animate-spin" : ""}`} />
                      </button>
                    )}
                    {canManage && s.posts_count > 0 && (
                      <button className="btn-ghost" disabled={analyzing} title="Проанализировать: метрики, разметка, эмбеддинги"
                              onClick={() => act(() => api(`/sources/${s.id}/analyze`, { method: "POST" }))}>
                        <Sparkles className="size-4" />
                      </button>
                    )}
                    {canManage && (
                      <button className="btn-ghost text-bad" title="Удалить источник"
                              onClick={() => confirm(`Удалить «${s.name || s.title || s.key}»?`) &&
                                act(() => api(`/sources/${s.id}`, { method: "DELETE" }))}>
                        <Trash2 className="size-4" />
                      </button>
                    )}
                  </div>
                </div>
                {s.posts_count > 0 && (
                  <div className="mt-3 border-t border-line pt-3">
                    <button className="flex items-center gap-1 text-sm text-muted hover:text-accent"
                            onClick={() => setOpen(open === s.id ? null : s.id)}>
                      {open === s.id ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                      Последние посты
                    </button>
                    {open === s.id && <div className="mt-3"><Posts sourceId={s.id} /></div>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
