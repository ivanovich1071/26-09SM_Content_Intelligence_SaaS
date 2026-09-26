"use client";

import { ArrowLeft, Loader2, RefreshCw, Sparkles } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PostList, SourceTag } from "@/components/PostList";
import { api, type PostDetail } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtNum, label, media, pct, times } from "@/lib/format";

function Row({ title, value }: { title: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div>
      <p className="label">{title}</p>
      <p className="text-sm">{value}</p>
    </div>
  );
}

function List({ title, items, tone }: { title: string; items: string[]; tone?: string }) {
  if (!items.length) return null;
  return (
    <div>
      <p className={`label ${tone ?? ""}`}>{title}</p>
      <ul className="list-disc space-y-1 pl-5 text-sm">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}

export default function PostPage() {
  const { id } = useParams<{ id: string }>();
  const { org } = useAuth();
  const canRun = org?.role !== "viewer";
  const [d, setD] = useState<PostDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<PostDetail>(`/posts/${id}`).then(setD).catch((e) => setError(e.message));
  }, [id]);

  async function analyze(force: boolean) {
    setBusy(true);
    setError(null);
    try {
      setD(await api<PostDetail>(`/posts/${id}/analyze${force ? "?force=true" : ""}`, { method: "POST" }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
    setBusy(false);
  }

  if (!d) return error ? <p className="text-sm text-bad">{error}</p> : <p className="text-sm text-muted">Загрузка…</p>;
  const p = d.post;
  const a = p.analysis && !p.analysis.error ? p.analysis : null;
  const ins = d.insight;
  const metrics = [
    ["Просмотры", fmtNum(p.views), d.source_median_views !== null ? `медиана канала ${fmtNum(Math.round(d.source_median_views))}` : null],
    ["Реакции", fmtNum(p.likes), null], ["Комментарии", fmtNum(p.comments), null], ["Репосты", fmtNum(p.shares), null],
    ["ER", pct(p.er), null],
    ["Отклик", p.overperformance !== null ? times(p.overperformance) : "—", "к медиане своего канала"],
  ];

  return (
    <>
      <Link href="/feed" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-accent">
        <ArrowLeft className="size-4" />Лента
      </Link>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <div className="card">
            <div className="flex flex-wrap gap-x-3 text-xs text-muted">
              <SourceTag post={p} />
              <span>{fmtDate(p.published_at)}</span>
              <span>{media(p.media_type)}</span>
              {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">оригинал</a>}
            </div>
            {p.title && <h1 className="mt-2 text-lg font-bold">{p.title}</h1>}
            <p className="mt-2 text-sm whitespace-pre-line">{p.text || "(без текста)"}</p>
          </div>

          <div className="card">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">AI-разбор</h2>
              {canRun && (
                <button className="btn-ghost" onClick={() => analyze(!!ins)} disabled={busy}>
                  {busy ? <Loader2 className="size-4 animate-spin" /> : ins ? <RefreshCw className="size-4" /> : <Sparkles className="size-4 text-accent" />}
                  {ins ? "Разобрать заново" : "Разобрать пост"}
                </button>
              )}
            </div>
            {error && <p className="mt-2 text-sm text-bad">{error}</p>}
            {!ins ? (
              <p className="mt-2 text-sm text-muted">Хук, боль, аргументация, почему такой отклик, что взять и чего не копировать. Один вызов модели, результат сохраняется.</p>
            ) : (
              <div className="mt-3 grid gap-4 md:grid-cols-2">
                <div className="space-y-3 md:col-span-2"><p className="text-sm">{ins.summary}</p></div>
                <Row title="Хук" value={ins.hook} />
                <Row title="Боль / желание" value={ins.pain_point} />
                <Row title="Аудитория" value={ins.audience} />
                <Row title="Аргументация" value={ins.argumentation} />
                <Row title="Призыв" value={ins.cta} />
                <Row title="Формат" value={ins.format_notes} />
                <div className="md:col-span-2"><Row title="Почему такой отклик" value={ins.why_it_worked} /></div>
                <List title="Что взять" items={ins.patterns_to_use} tone="text-good" />
                <List title="Не копировать" items={ins.do_not_copy} tone="text-bad" />
                {d.insight_at && <p className="text-xs text-muted md:col-span-2">Разбор от {fmtDate(d.insight_at)}</p>}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <div className="card">
            <p className="label">Метрики</p>
            <dl className="space-y-2 text-sm">
              {metrics.map(([t, v, hint]) => (
                <div key={t} className="flex justify-between gap-3">
                  <dt className="text-muted">{t}{hint && <span className="block text-xs">{hint}</span>}</dt>
                  <dd className="font-semibold">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
          {a && (
            <div className="card">
              <p className="label">Разметка</p>
              <dl className="space-y-1.5 text-sm">
                {([["Тема", a.topic], ["Аудитория", a.target_role], ["Тип", label(a.content_type)],
                   ["Воронка", label(a.funnel_stage)], ["Хук", label(a.hook_type)], ["CTA", label(a.cta_type)],
                   ["Доказательство", label(a.proof_type)], ["Тон", label(a.tone)]] as const).map(([t, v]) => (
                  <div key={t} className="flex justify-between gap-3"><dt className="text-muted">{t}</dt><dd>{v}</dd></div>
                ))}
              </dl>
            </div>
          )}
          {d.similar.length > 0 && (
            <div className="card">
              <p className="label">Похожие по смыслу</p>
              <PostList posts={d.similar} linked />
            </div>
          )}
        </div>
      </div>
    </>
  );
}
