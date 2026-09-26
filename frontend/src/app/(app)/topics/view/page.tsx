"use client";

import { ArrowLeft, Loader2, RefreshCw, Sparkles } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { GapInsightView } from "@/components/GapInsightView";
import { PostList } from "@/components/PostList";
import { ShareBars } from "@/components/ShareBars";
import { api, type Share, type TopicDetail } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtNum, label, media, pct } from "@/lib/format";

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

function Weekly({ weeks }: { weeks: TopicDetail["weekly"] }) {
  const max = Math.max(1, ...weeks.map((w) => w.own + w.competitor + w.market));
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <p className="label">Публикации по неделям</p>
        <div className="flex gap-3 text-xs text-muted">
          <span><i className="mr-1 inline-block size-2 rounded-full bg-accent" />вы</span>
          <span><i className="mr-1 inline-block size-2 rounded-full bg-warn" />конкуренты</span>
          <span><i className="mr-1 inline-block size-2 rounded-full bg-muted" />рынок</span>
        </div>
      </div>
      <div className="mt-3 flex h-36 items-end gap-1.5">
        {weeks.map((w) => {
          const total = w.own + w.competitor + w.market;
          return (
            <div key={w.week} className="flex h-full flex-1 flex-col justify-end" title={`с ${w.week}: вы ${w.own}, конкуренты ${w.competitor}, рынок ${w.market}`}>
              <div className="flex flex-col overflow-hidden rounded-t-md" style={{ height: `${(100 * total) / max}%` }}>
                <div className="bg-muted" style={{ flex: w.market }} />
                <div className="bg-warn" style={{ flex: w.competitor }} />
                <div className="bg-accent" style={{ flex: w.own }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TopicView() {
  const topic = useSearchParams().get("topic") ?? "";
  const { org } = useAuth();
  const canRun = org?.role !== "viewer";
  const [days, setDays] = useState(90);
  const [d, setD] = useState<TopicDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api<TopicDetail>(`/topics/detail?topic=${encodeURIComponent(topic)}&days=${days}`)
      .then(setD).catch((e) => { setD(null); setError(e.message); });
  }, [topic, days]);
  useEffect(load, [load]);

  async function explain() {
    setBusy(true);
    try {
      await api("/topics/gaps/explain", { method: "POST", json: { topic, days, force: !!d?.insight } });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
    setBusy(false);
  }

  const kpis = d ? [
    ["Публикаций рынка", fmtNum(d.market_total)], ["Ваших", fmtNum(d.own)],
    ["Gap", `${d.gap > 0 ? "+" : ""}${d.gap} п.п.`], ["Тренд рынка", d.trend_pp === null ? "—" : `${d.trend_pp > 0 ? "+" : ""}${d.trend_pp} п.п.`],
    ["В неделю (рынок)", fmtNum(d.saturation_per_week)], ["Медиана ER", pct(d.median_er)],
    ["Медиана вовлечённости", fmtNum(d.median_engagement)], ["Конкурентов в теме", fmtNum(d.competitors.length)],
  ] : [];

  return (
    <>
      <Link href="/topics" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-accent">
        <ArrowLeft className="size-4" />Все темы
      </Link>
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-2xl font-bold">{topic}</h1>
        <div className="flex gap-1">
          {[30, 90, 180].map((x) => (
            <button key={x} onClick={() => setDays(x)}
                    className={`rounded-lg px-2.5 py-1 text-xs ${x === days ? "bg-accent text-white" : "bg-surface text-muted"}`}>{x} дней</button>
          ))}
        </div>
      </header>
      {error && <p className="mb-4 text-sm text-bad">{error}</p>}
      {!d ? (!error && <p className="text-sm text-muted">Загрузка…</p>) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {kpis.map(([t, v]) => <div key={t} className="card"><p className="label">{t}</p><p className="text-xl font-bold">{v}</p></div>)}
          </div>

          <div className="card space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="font-semibold">Content Gap</h2>
                <p className="text-xs text-muted">Доля темы среди всех публикаций рынка и среди ваших за {days} дней</p>
              </div>
              {canRun && (
                <button className="btn-ghost" onClick={explain} disabled={busy}>
                  {busy ? <Loader2 className="size-4 animate-spin" /> : d.insight ? <RefreshCw className="size-4" /> : <Sparkles className="size-4 text-accent" />}
                  {d.insight ? "Объяснить заново" : "Объяснить"}
                </button>
              )}
            </div>
            <ShareBars own={d.share_own} market={d.share_market} />
            {d.insight && <div className="border-t border-line pt-3"><GapInsightView insight={d.insight} /></div>}
          </div>

          <Weekly weeks={d.weekly} />

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="card">
              <p className="label">Под-темы</p>
              {d.subtopics.length === 0 ? (
                <p className="text-sm text-muted">Не выделены: мало публикаций или тема однородна. Пересчёт — во вкладке «Темы».</p>
              ) : (
                <ul className="space-y-3 text-sm">
                  {d.subtopics.map((s) => (
                    <li key={s.id}>
                      <div className="flex justify-between gap-2">
                        <span className="font-medium">{s.label}</span>
                        <span className="text-xs text-muted">вы {s.own} · конк. {s.competitor} · рынок {s.market}</span>
                      </div>
                      {s.description && <p className="text-xs text-muted">{s.description}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="card space-y-4">
              <div>
                <p className="label">Конкуренты в теме</p>
                {d.by_competitor.length === 0 ? <p className="text-sm text-muted">Конкуренты тему не раскрывают</p> : (
                  <ul className="space-y-1 text-sm">{d.by_competitor.map((c) => (
                    <li key={c.name} className="flex justify-between"><span>{c.name}</span><span className="text-muted">{c.posts} публ.</span></li>
                  ))}</ul>
                )}
              </div>
              {d.related.length > 0 && (
                <div>
                  <p className="label">Связанные темы</p>
                  <div className="flex flex-wrap gap-2">
                    {d.related.map((r) => (
                      <Link key={r.topic} href={`/topics/view?topic=${encodeURIComponent(r.topic)}`}
                            className="rounded-lg bg-accent-soft px-2.5 py-1 text-sm text-accent hover:opacity-80">{r.topic}</Link>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Bars title="Форматы рынка" shares={d.formats_market} fmt={media} />
            <Bars title="Типы контента рынка" shares={d.content_types} />
            <Bars title="Хуки рынка" shares={d.hooks} />
          </div>

          <div className="card">
            <p className="label">Лучшие публикации рынка и конкурентов</p>
            <PostList posts={d.top_posts} linked />
          </div>
          {d.own_posts.length > 0 && (
            <div className="card">
              <p className="label">Ваши публикации по теме</p>
              <PostList posts={d.own_posts} linked />
            </div>
          )}
        </div>
      )}
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="text-sm text-muted">Загрузка…</p>}><TopicView /></Suspense>;
}
