"use client";

import { ArrowLeft, Loader2, Plus, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PostList } from "@/components/PostList";
import { api, type Competitor, type ContentStats, type Post, type Share } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtNum, KIND_ICON, KIND_LABELS, label, media, pct } from "@/lib/format";

const TABS = [
  { id: "profile", title: "Профиль" },
  { id: "analytics", title: "Аналитика" },
  { id: "timeline", title: "Таймлайн" },
  { id: "content", title: "Контент" },
] as const;
type Tab = (typeof TABS)[number]["id"];
const ACTIVE = new Set(["queued", "running", "analyzing", "collecting"]);

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="label">{title}</p>
      <ul className="list-disc space-y-1 pl-5 text-sm">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}

function Field({ title, value }: { title: string; value: string }) {
  if (!value) return null;
  return (
    <div>
      <p className="label">{title}</p>
      <p className="text-sm">{value}</p>
    </div>
  );
}

function Profile({ c, canManage, onRefresh }: { c: Competitor; canManage: boolean; onRefresh: () => void }) {
  const job = c.profile_job;
  const building = !!job && ACTIVE.has(job.status);
  const ai = c.profile?.ai;
  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          {c.profile_at ? `Профиль построен ${fmtDate(c.profile_at)} по ${c.profile?.stats.posts ?? 0} публикациям за ${c.profile?.stats.days} дней.`
            : "Профиля ещё нет: он строится сам, когда у конкурента накопится минимум 5 размеченных постов."}
          {job?.status === "failed" && <span className="block text-bad">{job.error}</span>}
        </p>
        {canManage && (
          <button className="btn-ghost" onClick={onRefresh} disabled={building}>
            {building ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4 text-accent" />}
            {building ? "Строим профиль…" : c.profile ? "Обновить профиль" : "Построить профиль"}
          </button>
        )}
      </div>
      {ai && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="card space-y-4">
            <p className="text-sm">{ai.summary}</p>
            <Field title="Позиционирование" value={ai.positioning} />
            <Field title="Аудитория" value={ai.audience} />
            <Field title="Tone of voice" value={ai.tone_of_voice} />
            <Field title="Частота" value={ai.posting_frequency} />
            <Bullets title="Главные темы" items={ai.main_topics} />
            <Bullets title="Форматы" items={ai.formats} />
            <Bullets title="Призывы" items={ai.ctas} />
          </div>
          <div className="card space-y-4">
            <Bullets title="Контентные паттерны" items={ai.content_patterns} />
            <Bullets title="Что работает" items={ai.strengths} />
            <Bullets title="Слабые места" items={ai.weaknesses} />
            <Bullets title="Что взять вам (без копирования)" items={ai.opportunities} />
          </div>
        </div>
      )}
    </div>
  );
}

function Bars({ title, shares, fmt = label }: { title: string; shares: Share[]; fmt?: (v: string) => string | null }) {
  return (
    <div className="card">
      <p className="label">{title}</p>
      {shares.length === 0 ? <p className="text-sm text-muted">Нет данных</p> : (
        <ul className="space-y-2 text-sm">
          {shares.map((s) => (
            <li key={s.value}>
              <div className="flex justify-between gap-2"><span>{fmt(s.value)}</span><span className="text-muted">{s.share}% · {s.count}</span></div>
              <div className="mt-1 h-1.5 rounded-full bg-bg"><div className="h-1.5 rounded-full bg-accent" style={{ width: `${s.share}%` }} /></div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Analytics({ st }: { st: ContentStats }) {
  const kpis = [
    ["Публикаций", fmtNum(st.posts)], ["В неделю", fmtNum(st.posts_per_week)],
    ["Медиана просмотров", fmtNum(st.median_views === null ? null : Math.round(st.median_views))],
    ["Медиана ER", pct(st.median_er)], ["С призывом", pct(st.cta_share)], ["С кейсами", pct(st.case_share)],
    ["С цифрами", pct(st.numbers_share)], ["Продающих", pct(st.offer_share)],
  ];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {kpis.map(([t, v]) => (
          <div key={t} className="card"><p className="label">{t}</p><p className="text-xl font-bold">{v}</p></div>
        ))}
      </div>
      {st.analyzed < st.posts && (
        <p className="text-xs text-muted">Размечено {st.analyzed} из {st.posts} публикаций — доли тем и форматов считаются по размеченным.</p>
      )}
      <div className="grid gap-4 lg:grid-cols-3">
        <Bars title="Темы" shares={st.topics} />
        <Bars title="Форматы" shares={st.formats} fmt={media} />
        <Bars title="Типы контента" shares={st.content_types} />
        <Bars title="Стадии воронки" shares={st.funnel} />
        <Bars title="Хуки" shares={st.hooks} />
        <Bars title="Призывы" shares={st.ctas} />
      </div>
    </div>
  );
}

function Timeline({ st }: { st: ContentStats }) {
  const max = Math.max(1, ...st.weekly.map((w) => w.posts));
  return (
    <div className="card">
      <p className="label">Публикации по неделям</p>
      <div className="mt-3 flex h-40 items-end gap-2">
        {st.weekly.map((w) => (
          <div key={w.week} className="flex flex-1 flex-col items-center gap-1" title={`Неделя с ${w.week}: ${w.posts} публ., медиана ER ${pct(w.median_er)}`}>
            <span className="text-xs text-muted">{w.posts || ""}</span>
            <div className="w-full rounded-t-md bg-accent" style={{ height: `${(100 * w.posts) / max}%`, minHeight: w.posts ? 4 : 0 }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-2 text-[10px] text-muted">
        {st.weekly.map((w) => <span key={w.week} className="flex-1 text-center">{w.week.slice(5)}</span>)}
      </div>
      <p className="mt-3 text-xs text-muted">Изменения сайта конкурента появятся здесь вместе с вкладкой «Сайты» (EPIC 7).</p>
    </div>
  );
}

function Content({ id }: { id: number }) {
  const [sort, setSort] = useState<"recent" | "top">("top");
  const [posts, setPosts] = useState<Post[] | null>(null);
  useEffect(() => {
    api<Post[]>(`/competitors/${id}/posts?sort=${sort}&limit=30`).then(setPosts).catch(() => setPosts([]));
  }, [id, sort]);
  return (
    <div className="card">
      <div className="mb-4 flex gap-2">
        {(["top", "recent"] as const).map((s) => (
          <button key={s} onClick={() => setSort(s)}
                  className={`rounded-lg px-3 py-1 text-sm ${s === sort ? "bg-accent text-white" : "bg-bg text-muted"}`}>
            {s === "top" ? "Лучшие" : "Свежие"}
          </button>
        ))}
      </div>
      {posts === null ? <p className="text-sm text-muted">Загрузка…</p> : <PostList posts={posts} />}
    </div>
  );
}

export default function CompetitorPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { org } = useAuth();
  const canManage = org?.role !== "viewer";
  const [c, setC] = useState<Competitor | null>(null);
  const [tab, setTab] = useState<Tab>("profile");
  const [days, setDays] = useState(90);
  const [newSource, setNewSource] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Competitor>(`/competitors/${id}?days=${days}`).then(setC).catch((e) => setError(e.message));
  }, [id, days]);
  useEffect(load, [load]);

  const busy = !!c && ([c.profile_job, ...c.sources.flatMap((s) => [s.last_job, s.last_analysis])]
    .some((j) => j && ACTIVE.has(j.status)));
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [busy, load]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  if (!c) return error ? <p className="text-sm text-bad">{error}</p> : <p className="text-sm text-muted">Загрузка…</p>;

  return (
    <>
      <Link href="/competitors" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-accent">
        <ArrowLeft className="size-4" />Все конкуренты
      </Link>
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{c.name}</h1>
          {c.website && <a href={c.website} target="_blank" rel="noreferrer" className="text-sm text-muted hover:text-accent">{c.website}</a>}
          {c.notes && <p className="mt-1 max-w-2xl text-sm text-muted">{c.notes}</p>}
        </div>
        {canManage && (
          <button className="btn-ghost text-bad" onClick={() => confirm(`Удалить «${c.name}» и его каналы?`) &&
            act(async () => { await api(`/competitors/${c.id}`, { method: "DELETE" }); router.push("/competitors"); })}>
            <Trash2 className="size-4" />Удалить
          </button>
        )}
      </header>

      <div className="card mb-4">
        <div className="flex flex-wrap gap-3">
          {c.sources.map((s) => {
            const Icon = KIND_ICON[s.kind];
            const syncing = [s.last_job, s.last_analysis].some((j) => j && ACTIVE.has(j.status));
            return (
              <span key={s.id} className="inline-flex items-center gap-1.5 rounded-lg bg-bg px-2.5 py-1 text-sm"
                    title={s.last_error ?? `${KIND_LABELS[s.kind]} · постов ${s.posts_count}`}>
                <Icon className={`size-4 ${s.status === "ok" ? "text-accent" : s.status === "new" ? "text-muted" : "text-bad"}`} />
                {s.title || s.key}
                {s.followers !== null && <span className="text-muted">· {fmtNum(s.followers)}</span>}
                {syncing && <RefreshCw className="size-3 animate-spin text-muted" />}
              </span>
            );
          })}
          {c.sources.length === 0 && <span className="text-sm text-muted">Каналов нет — добавьте хотя бы один.</span>}
        </div>
        {canManage && (
          <form className="mt-3 flex gap-2" onSubmit={(e) => {
            e.preventDefault();
            act(async () => {
              await api(`/competitors/${c.id}/sources`, { method: "POST", json: { url: newSource } });
              setNewSource("");
            });
          }}>
            <input className="input" value={newSource} onChange={(e) => setNewSource(e.target.value)}
                   placeholder="Добавить канал: t.me/…, instagram.com/…, youtube.com/@…, vk.com/…" />
            <button className="btn-ghost" disabled={!newSource}><Plus className="size-4" />Канал</button>
          </form>
        )}
        {error && <p className="mt-2 text-sm text-bad">{error}</p>}
      </div>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-line">
        <nav className="flex gap-1">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
                    className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t.id ? "border-accent font-semibold text-accent" : "border-transparent text-muted hover:text-ink"}`}>
              {t.title}
            </button>
          ))}
        </nav>
        {(tab === "analytics" || tab === "timeline") && (
          <div className="flex gap-1 pb-1">
            {[30, 90].map((d) => (
              <button key={d} onClick={() => setDays(d)}
                      className={`rounded-lg px-2.5 py-1 text-xs ${d === days ? "bg-accent text-white" : "bg-surface text-muted"}`}>{d} дней</button>
            ))}
          </div>
        )}
      </div>

      {tab === "profile" && <Profile c={c} canManage={canManage}
                                     onRefresh={() => act(() => api(`/competitors/${c.id}/profile`, { method: "POST" }))} />}
      {tab === "analytics" && <Analytics st={c.stats} />}
      {tab === "timeline" && <Timeline st={c.stats} />}
      {tab === "content" && <Content id={c.id} />}
    </>
  );
}
