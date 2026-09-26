"use client";

import { ChevronDown, ChevronRight, Loader2, RefreshCw, Sparkles, TrendingDown, TrendingUp } from "lucide-react";
import Link from "next/link";
import { Fragment, useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { GapInsightView } from "@/components/GapInsightView";
import { ShareBars } from "@/components/ShareBars";
import { api, type GapInsight, type GapsResponse, type TopicRow, type TopicsOverview } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, pct } from "@/lib/format";

const ACTIVE = new Set(["queued", "running", "analyzing"]);
const topicHref = (t: string) => `/topics/view?topic=${encodeURIComponent(t)}`;

function Trend({ pp }: { pp: number | null }) {
  if (pp === null || Math.abs(pp) < 0.5) return <span className="text-muted">—</span>;
  const up = pp > 0;
  const Icon = up ? TrendingUp : TrendingDown;
  return <span className={`inline-flex items-center gap-1 ${up ? "text-good" : "text-bad"}`}><Icon className="size-3.5" />{up ? "+" : ""}{pp} п.п.</span>;
}

function Explorer({ data }: { data: TopicsOverview }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (t: string) => setOpen((s) => { const n = new Set(s); if (n.has(t)) n.delete(t); else n.add(t); return n; });
  if (data.topics.length === 0) {
    return <div className="card text-sm text-muted">Размеченных публикаций за период нет — подключите источники и дождитесь анализа.</div>;
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="text-left text-xs text-muted">
          <tr><th className="py-2">Тема</th><th className="w-64">Доля: рынок / вы</th><th>Публикаций</th><th>Тренд рынка</th><th>В неделю</th><th>Конкуренты</th><th>Медиана ER</th></tr>
        </thead>
        <tbody>
          {data.topics.map((t) => {
            const subs = t.subtopics ?? [];
            const isOpen = open.has(t.topic);
            return (
              <Fragment key={t.topic}>
                <tr className="border-t border-line align-top">
                  <td className="py-2.5 pr-3">
                    <div className="flex items-start gap-1">
                      {subs.length > 0 ? (
                        <button onClick={() => toggle(t.topic)} className="mt-0.5 text-muted hover:text-accent" title="Под-темы">
                          {isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                        </button>
                      ) : <span className="w-4" />}
                      <div>
                        <Link href={topicHref(t.topic)} className="font-medium hover:text-accent">{t.topic}</Link>
                        {t.is_gap && <span className="ml-2 rounded-md bg-warn/10 px-1.5 py-0.5 text-xs text-warn">gap +{t.gap} п.п.</span>}
                        {subs.length > 0 && <p className="text-xs text-muted">{subs.length} под-тем</p>}
                      </div>
                    </div>
                  </td>
                  <td className="py-2.5 pr-3"><ShareBars own={t.share_own} market={t.share_market} /></td>
                  <td className="py-2.5">{t.market_total} <span className="text-muted">/ ваших {t.own}</span></td>
                  <td className="py-2.5"><Trend pp={t.trend_pp} /></td>
                  <td className="py-2.5">{t.saturation_per_week}</td>
                  <td className="py-2.5">{t.competitors.length || "—"}</td>
                  <td className="py-2.5">{pct(t.median_er)}</td>
                </tr>
                {isOpen && subs.map((s) => (
                  <tr key={s.id} className="text-muted">
                    <td className="py-1 pl-9" colSpan={7}>└ {s.label} <span className="text-xs">· {s.size} публ.</span></td>
                  </tr>
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Gaps({ days, canRun }: { days: number; canRun: boolean }) {
  const [data, setData] = useState<GapsResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<GapsResponse>(`/topics/gaps?days=${days}`).then(setData).catch((e) => setError(e.message));
  }, [days]);
  useEffect(load, [load]);

  async function explain(topic: string, force: boolean) {
    setBusy(topic);
    setError(null);
    try {
      await api<GapInsight>("/topics/gaps/explain", { method: "POST", json: { topic, days, force } });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
    setBusy(null);
  }

  if (!data) return <p className="text-sm text-muted">Загрузка…</p>;
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        Темы, где доля рынка выше вашей минимум на {data.threshold_pp} п.п. За {data.days} дней: ваших публикаций {data.own_total},
        рынка и конкурентов {data.market_total}.
      </p>
      {error && <p className="text-sm text-bad">{error}</p>}
      {data.gaps.length === 0 && <div className="card text-sm text-muted">Пробелов не найдено — ваш контент покрывает темы рынка.</div>}
      {data.gaps.map((g) => (
        <div key={g.topic} className="card space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <Link href={topicHref(g.topic)} className="font-semibold hover:text-accent">{g.topic}</Link>
              <p className="text-xs text-muted">
                gap +{g.gap} п.п. · рынок {g.market_total} публ. ({g.saturation_per_week}/нед) · у вас {g.own}
                {g.competitors.length > 0 && ` · раскрывают: ${g.competitors.join(", ")}`}
              </p>
            </div>
            {canRun && (
              <button className="btn-ghost" onClick={() => explain(g.topic, !!g.insight)} disabled={busy !== null}>
                {busy === g.topic ? <Loader2 className="size-4 animate-spin" /> : g.insight ? <RefreshCw className="size-4" /> : <Sparkles className="size-4 text-accent" />}
                {g.insight ? "Объяснить заново" : "Объяснить gap"}
              </button>
            )}
          </div>
          <ShareBars own={g.share_own} market={g.share_market} />
          {g.insight && <div className="border-t border-line pt-3"><GapInsightView insight={g.insight} /></div>}
        </div>
      ))}
    </div>
  );
}

export default function TopicsPage() {
  const { org } = useAuth();
  const canRun = org?.role !== "viewer";
  const [tab, setTab] = useState<"explorer" | "gaps">("explorer");
  const [days, setDays] = useState(30);
  const [data, setData] = useState<TopicsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<TopicsOverview>(`/topics?days=${days}`).then(setData).catch((e) => setError(e.message));
  }, [days]);
  useEffect(load, [load]);
  const clustering = !!data?.cluster_job && ACTIVE.has(data.cluster_job.status);
  useEffect(() => {
    if (!clustering) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [clustering, load]);

  async function recluster() {
    setError(null);
    try {
      await api("/topics/cluster", { method: "POST" });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  const job = data?.cluster_job;
  return (
    <>
      <PageHeader title="Темы"
                  subtitle="Темы вашей таксономии: доли у вас и у рынка, тренд, насыщенность и Content Gaps. Под-темы выделяются автоматически по смыслу публикаций." />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-line">
        <nav className="flex gap-1">
          {([["explorer", "Дерево тем"], ["gaps", "Content Gaps"]] as const).map(([id, title]) => (
            <button key={id} onClick={() => setTab(id)}
                    className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === id ? "border-accent font-semibold text-accent" : "border-transparent text-muted hover:text-ink"}`}>
              {title}
            </button>
          ))}
        </nav>
        <div className="flex items-center gap-2 pb-1">
          {[30, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)}
                    className={`rounded-lg px-2.5 py-1 text-xs ${d === days ? "bg-accent text-white" : "bg-surface text-muted"}`}>{d} дней</button>
          ))}
        </div>
      </div>
      {tab === "explorer" && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
          <span>
            {job?.status === "completed" && `Под-темы пересчитаны ${fmtDate(job.finished_at)}.`}
            {job?.status === "failed" && <span className="text-bad">{job.error}</span>}
            {clustering && "Пересчитываем под-темы…"}
            {!job && "Под-темы ещё не считались — пересчёт идёт раз в неделю автоматически или по кнопке."}
          </span>
          {canRun && (
            <button className="btn-ghost" onClick={recluster} disabled={clustering}>
              {clustering ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}Пересчитать под-темы
            </button>
          )}
        </div>
      )}
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      {tab === "explorer" && (data ? <Explorer data={data} /> : <p className="text-sm text-muted">Загрузка…</p>)}
      {tab === "gaps" && <Gaps days={days} canRun={canRun} />}
    </>
  );
}
