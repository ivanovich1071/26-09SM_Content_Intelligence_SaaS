"use client";

import { ArrowLeft, Download, ExternalLink, Factory, Mail, Printer, Sparkles } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, download, type ContentProject, type Digest, type DigestPost } from "@/lib/api";
import { FORMAT_NAMES } from "@/lib/audit";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtNum, media, pct } from "@/lib/format";

const delta = (d: number | null) => (d === null ? "" : ` (${d > 0 ? "+" : ""}${d}%)`);

function Posts({ posts }: { posts: DigestPost[] }) {
  if (posts.length === 0) return <p className="text-sm text-muted">Нет.</p>;
  return (
    <ul className="space-y-2 text-sm">
      {posts.map((p) => (
        <li key={p.post_id}>
          <p className="line-clamp-2">{p.text}</p>
          <p className="text-xs text-muted">
            {p.source} · {p.date} · {media(p.format)}{p.overperformance ? ` · ×${p.overperformance.toFixed(1)} к обычному` : ""}
            {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-0.5 text-accent">открыть<ExternalLink className="size-3" /></a>}
          </p>
        </li>
      ))}
    </ul>
  );
}

function Section({ title, text, children }: { title: string; text?: string; children?: React.ReactNode }) {
  return (
    <div className="card break-inside-avoid">
      <h2 className="font-semibold">{title}</h2>
      {text && <p className="mt-1 text-sm whitespace-pre-line">{text}</p>}
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}

export default function DigestView() {
  const { id } = useParams<{ id: string }>();
  const { org } = useAuth();
  const router = useRouter();
  const canManage = org?.role !== "viewer";
  const [d, setD] = useState<Digest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [to, setTo] = useState("");
  const [sent, setSent] = useState<string | null>(null);

  useEffect(() => {
    api<Digest>(`/digests/${id}`).then(setD).catch((e) => setError(e.message));
  }, [id]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSent(null);
    try {
      await api(`/digests/${id}/send`, { method: "POST", json: { recipients: to.split(/[\s,;]+/).filter(Boolean) } });
      setSent("Отправлено");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
    }
  }

  async function toFactory(title: string, why: string, format: string) {
    try {
      const p = await api<ContentProject>("/factory/projects", { method: "POST", json: { title, brief: why, format, generate: false } });
      router.push(`/factory/${p.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  if (!d) return <p className="text-sm text-muted">{error ?? "Загрузка…"}</p>;
  const st = d.stats, s = d.sections;
  return (
    <>
      <Link href="/digest" className="no-print mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-accent"><ArrowLeft className="size-4" />Все выпуски</Link>
      <div className="mb-4 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-muted">{d.title}</p>
          <h1 className="text-2xl font-bold">{d.ai && <Sparkles className="mr-1 inline size-5 text-accent" />}{s.headline}</h1>
          <p className="mt-2 max-w-3xl">{s.summary}</p>
        </div>
        <div className="no-print flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={() => window.print()}><Printer className="size-4" />PDF</button>
          <button className="btn-ghost" onClick={() => download(`/digests/${id}/export?type=md`, `digest-${id}.md`)}><Download className="size-4" />.md</button>
          <button className="btn-ghost" onClick={() => download(`/digests/${id}/export?type=html`, `digest-${id}.html`)}><Download className="size-4" />.html</button>
        </div>
      </div>
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[["Публикаций рынка", `${fmtNum(st.market.posts)}${delta(st.market.delta_pct)}`],
          ["ER рынка, медиана", `${pct(st.market.median_er)} (было ${pct(st.market.prev_median_er)})`],
          ["Ваших публикаций", `${fmtNum(st.own.posts)}${delta(st.own.delta_pct)}`],
          ["Ваш ER, медиана", `${pct(st.own.median_er)} (было ${pct(st.own.prev_median_er)})`]].map(([k, v]) => (
          <div key={k} className="card"><p className="text-xs text-muted">{k}</p><p className="text-lg font-bold">{v}</p></div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Рынок" text={s.market} />
        <Section title="Темы" text={s.topics}>
          <div className="grid gap-3 text-sm sm:grid-cols-3">
            <div><p className="label">Растут</p>{st.topics.rising.length ? st.topics.rising.map((t) => <p key={t.topic}>{t.topic} <span className="text-good">+{t.trend_pp} п.п.</span></p>) : <p className="text-muted">—</p>}</div>
            <div><p className="label">Новые</p>{st.topics.new.length ? st.topics.new.map((t) => <p key={t.topic}>{t.topic}</p>) : <p className="text-muted">—</p>}</div>
            <div><p className="label">Пробелы</p>{st.topics.gaps.length ? st.topics.gaps.map((t) => <p key={t.topic}>{t.topic} <span className="text-warn">+{t.gap} п.п.</span></p>) : <p className="text-muted">—</p>}</div>
          </div>
        </Section>
        <Section title="Конкуренты" text={s.competitors}>
          {st.competitors.length === 0 ? <p className="text-sm text-muted">Нет активности.</p> : (
            <ul className="space-y-3 text-sm">
              {st.competitors.map((c) => (
                <li key={c.id}>
                  <p><Link href={`/competitors/${c.id}`} className="font-semibold hover:text-accent">{c.name}</Link>
                    <span className="text-muted"> · публикаций {c.posts} (было {c.prev_posts}) · ER {pct(c.median_er)}</span></p>
                  {c.new_formats.length > 0 && <p className="text-xs text-warn">Новый формат: {c.new_formats.map(media).join(", ")}</p>}
                  {c.site_changes.map((ch, i) => (
                    <p key={i} className="text-xs">Сайт: <a href={ch.url} target="_blank" rel="noreferrer" className="text-accent">{ch.page}</a> — {ch.summary}</p>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title="Лучшие публикации" text={s.top_posts}><Posts posts={st.top_posts} /></Section>
        <Section title="Необычные ходы" text={s.unusual}>{st.outliers.length > 0 && <Posts posts={st.outliers} />}</Section>
        <Section title="Ваш контент" text={s.own}>{st.own.best_post && <Posts posts={[st.own.best_post]} />}</Section>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="Что делать">
          <ol className="list-decimal space-y-2 pl-5 text-sm">
            {s.recommendations.map((r, i) => <li key={i}><b>{r.title}</b>{r.why && <> — {r.why}</>}</li>)}
          </ol>
        </Section>
        <Section title="Идеи для Контент Завода">
          <ul className="space-y-2 text-sm">
            {s.ideas.map((i, n) => (
              <li key={n} className="flex items-start gap-2">
                <span className="min-w-0 flex-1"><b>{i.title}</b> <span className="text-xs text-muted">{FORMAT_NAMES[i.format] ?? i.format}</span>
                  {i.why && <span className="block text-muted">{i.why}</span>}</span>
                {canManage && <button className="btn-ghost no-print shrink-0" onClick={() => toFactory(i.title, i.why, i.format)}><Factory className="size-4" /></button>}
              </li>
            ))}
            {s.ideas.length === 0 && <p className="text-muted">Нет идей — мало данных за период.</p>}
          </ul>
        </Section>
      </div>

      {canManage && (
        <form onSubmit={send} className="card no-print mt-4 flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <label className="label" htmlFor="to">Отправить на email</label>
            <input id="to" className="input" value={to} placeholder="ceo@company.by" onChange={(e) => setTo(e.target.value)} />
          </div>
          <button className="btn" disabled={!to.trim()}><Mail className="size-4" />Отправить</button>
          {sent && <span className="text-sm text-good">{sent}</span>}
          {d.emailed_at && <span className="text-xs text-muted">Последняя отправка: {fmtDate(d.emailed_at)}</span>}
        </form>
      )}
    </>
  );
}
