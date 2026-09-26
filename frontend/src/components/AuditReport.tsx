"use client";

import { Check, CircleAlert, ExternalLink, Loader2, Lock, Printer, Sparkles } from "lucide-react";
import type { Audit, AuditItem, AuditStatus, BenchmarkRow } from "@/lib/api";
import { STAGES, scoreTone } from "@/lib/audit";
import { KIND_ICON, KIND_LABELS, fmtDate, fmtNum, pct } from "@/lib/format";

const ORIGIN: Record<string, string> = { input: "указан", own: "свой источник", site: "найден на сайте" };

/** Этапы аудита: источники → сбор → разметка → метрики → оценка. */
export function AuditProgress({ status, stage, progress, error }:
  { status: AuditStatus; stage: string; progress: number; error: string | null }) {
  const current = STAGES.findIndex((s) => s.key === stage);
  return (
    <div className="card">
      <ol className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        {STAGES.map((s, i) => {
          const done = status === "completed" || i < current;
          const active = status !== "failed" && i === current;
          return (
            <li key={s.key} className={`flex items-center gap-1.5 ${done ? "text-good" : active ? "font-semibold" : "text-muted"}`}>
              {done ? <Check className="size-4" /> : active ? <Loader2 className="size-4 animate-spin" /> :
                <span className="inline-block size-4 rounded-full border border-line" />}
              {s.label}
            </li>
          );
        })}
      </ol>
      {status !== "failed" && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-bg">
          <div className="h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${progress}%` }} />
        </div>
      )}
      {status === "queued" && <p className="mt-2 text-sm text-muted">В очереди…</p>}
      {status === "failed" && <p className="mt-3 text-sm text-bad">{error ?? "Аудит не удался"}</p>}
    </div>
  );
}

function Bar({ value }: { value: number | null }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-bg">
      {value !== null && <div className={`h-full rounded-full ${value >= 7 ? "bg-good" : value >= 4.5 ? "bg-warn" : "bg-bad"}`}
                               style={{ width: `${value * 10}%` }} />}
    </div>
  );
}

function Criterion({ item }: { item: AuditItem }) {
  return (
    <div className="card break-inside-avoid">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-semibold">{item.name}</h3>
        <span className={`text-lg font-extrabold ${scoreTone(item.score)}`}>
          {item.score === null ? "—" : item.score.toLocaleString("ru-RU")}<span className="text-xs font-normal text-muted"> / 10</span>
        </span>
      </div>
      <div className="mt-2"><Bar value={item.score} /></div>
      {item.locked ? (
        <p className="mt-3 flex items-center gap-1.5 text-sm text-muted"><Lock className="size-4" />Пояснение и советы — после регистрации</p>
      ) : (
        <>
          <p className="mt-3 text-sm">
            {item.ai && <Sparkles className="mr-1 inline size-3.5 text-accent" />}
            {item.score === null && !item.explanation ? "Недостаточно данных" : item.explanation}
          </p>
          {item.evidence.length > 0 && (
            <ul className="mt-3 space-y-1.5 text-sm">
              {item.evidence.map((e, i) => (
                <li key={i} className="border-l-2 border-line pl-2 text-muted">
                  {e.fact}
                  {e.url && (
                    <a href={e.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-0.5 text-accent">
                      пост{e.date ? ` от ${e.date}` : ""}<ExternalLink className="size-3" />
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
          {item.recommendations.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
              {item.recommendations.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

const num = (v: number | null, key: string) => (v === null ? "—" : key === "median_er" || key.endsWith("_share") ? pct(v) : fmtNum(v));

function Benchmark({ rows }: { rows: BenchmarkRow[] }) {
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-muted">
        <tr><th className="py-1 font-normal">Показатель</th><th className="pl-3 font-normal">Вы</th>
          <th className="pl-3 font-normal">Медиана рынка</th><th className="pl-3 font-normal">Сильные публикации</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const worse = r.own !== null && r.market !== null && r.own < r.market;
          return (
            <tr key={r.key} className="border-t border-line">
              <td className="py-1.5">{r.label}</td>
              <td className={`whitespace-nowrap pl-3 font-semibold ${worse ? "text-bad" : ""}`}>{num(r.own, r.key)}</td>
              <td className="whitespace-nowrap pl-3">{num(r.market, r.key)}</td>
              <td className="whitespace-nowrap pl-3 text-muted">{num(r.top, r.key)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Отчёт аудита: общий балл, критерии, проблемы, benchmark, gaps, каналы. Публичная версия частично скрыта. */
export function AuditReport({ audit, lockedCta }: { audit: Audit; lockedCta?: React.ReactNode }) {
  const r = audit.result;
  const bm = r.benchmark;
  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-start gap-6">
        <div className="text-center">
          <p className={`text-5xl font-extrabold ${scoreTone(audit.score, 100)}`}>{audit.score === null ? "—" : Math.round(audit.score)}</p>
          <p className="text-xs text-muted">из 100</p>
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-xl font-bold">{audit.company}</h2>
          <p className="text-sm text-muted">
            {audit.website && <>{audit.website} · </>}
            {fmtDate(audit.finished_at ?? audit.created_at)} · публикации за {r.days ?? 90} дней
          </p>
          {r.summary && <p className="mt-3"><Sparkles className="mr-1 inline size-4 text-accent" />{r.summary}</p>}
          {(r.notes ?? []).map((n) => <p key={n} className="mt-2 text-sm text-warn">{n}</p>)}
        </div>
        <button className="btn-ghost no-print" onClick={() => window.print()}><Printer className="size-4" />PDF</button>
      </div>

      {lockedCta}

      <div className="grid items-start gap-4 md:grid-cols-2">
        {audit.items.map((i) => <Criterion key={i.criterion} item={i} />)}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="card">
          <h2 className="font-semibold">Главные проблемы</h2>
          {(r.problems ?? []).length === 0 ? <p className="mt-2 text-sm text-muted">Критичных проблем не найдено.</p> : (
            <ol className="mt-2 space-y-2 text-sm">
              {r.problems!.map((p, i) => (
                <li key={i} className="flex gap-2"><CircleAlert className="mt-0.5 size-4 shrink-0 text-bad" />
                  <span><b>{p.title}</b>{p.detail && <> — {p.detail}</>}</span></li>
              ))}
            </ol>
          )}
          {!!r.problems_hidden && <p className="mt-2 flex items-center gap-1 text-sm text-muted"><Lock className="size-3.5" />Ещё {r.problems_hidden} — после регистрации</p>}
          {(r.strengths ?? []).length > 0 && (
            <>
              <h3 className="mt-4 text-sm font-semibold">Что работает</h3>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">{r.strengths!.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </>
          )}
        </div>

        <div className="card">
          <h2 className="font-semibold">Сравнение с рынком</h2>
          {bm?.enough && bm.rows ? <div className="mt-2"><Benchmark rows={bm.rows} /></div> :
            bm?.enough ? <p className="mt-2 flex items-center gap-1 text-sm text-muted"><Lock className="size-3.5" />Таблица — после регистрации</p> :
            <p className="mt-2 text-sm text-muted">{bm?.message ?? "Недостаточно данных для надёжного рыночного сравнения."}</p>}
          <h3 className="mt-4 text-sm font-semibold">Content Gaps — рынок пишет, вы нет</h3>
          {(r.gaps ?? []).length > 0 ? (
            <ul className="mt-1 space-y-1 text-sm">
              {r.gaps!.map((g) => (
                <li key={g.topic} className="flex justify-between gap-2">
                  <span>{g.topic}</span>
                  <span className="text-muted">рынок {pct(g.share_market)} · вы {pct(g.share_own)} · <b className="text-warn">+{g.gap} п.п.</b></span>
                </li>
              ))}
            </ul>
          ) : r.gaps_hidden ? <p className="mt-1 flex items-center gap-1 text-sm text-muted"><Lock className="size-3.5" />Найдено тем: {r.gaps_hidden} — после регистрации</p> :
            <p className="mt-1 text-sm text-muted">{bm?.enough ? "Пробелов по темам не найдено." : "Появятся, когда наберётся данных рынка."}</p>}
        </div>
      </div>

      <div className="card">
        <h2 className="font-semibold">Что проанализировано</h2>
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr><th className="py-1 font-normal">Канал</th><th className="font-normal">Публикаций</th><th className="font-normal">В неделю</th>
              <th className="font-normal">ER, медиана</th><th className="font-normal">Последний пост</th></tr>
          </thead>
          <tbody>
            {(r.channels ?? []).map((c) => {
              const Icon = KIND_ICON[c.kind];
              return (
                <tr key={c.url} className="border-t border-line align-top">
                  <td className="py-1.5">
                    <a href={c.url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 hover:text-accent">
                      <Icon className="size-4 shrink-0" />{c.title || c.url}
                    </a>
                    <span className="text-xs text-muted">{KIND_LABELS[c.kind]} · {ORIGIN[c.origin]}{c.followers ? ` · ${fmtNum(c.followers)} подписчиков` : ""}</span>
                    {(c.error || c.message) && <p className={`text-xs ${c.error ? "text-bad" : "text-muted"}`}>{c.error ?? c.message}</p>}
                  </td>
                  <td>{c.posts}{c.analyzed ? <span className="text-xs text-muted"> ({c.analyzed} разм.)</span> : null}</td>
                  <td>{c.posts_per_week}</td>
                  <td>{pct(c.median_er)}</td>
                  <td>{c.days_since_last_post === null ? "—" : `${c.days_since_last_post} дн. назад`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {r.site && (
          <div className="mt-4 text-sm">
            <p className="font-semibold">Сайт</p>
            {r.site.error ? <p className="text-bad">{r.site.error}</p> : (
              <p className="text-muted">
                Страниц: {r.site.pages.length} · форм заявки: {r.site.forms} · ссылок для связи: {r.site.contacts}
                {r.site.social_links.length > 0 && <> · соцсети: {r.site.social_links.length}</>}
              </p>
            )}
          </div>
        )}
      </div>

      {(r.top_posts ?? []).length > 0 && (
        <div className="card">
          <h2 className="font-semibold">Лучшие публикации</h2>
          <ul className="mt-2 space-y-2 text-sm">
            {r.top_posts!.map((p) => (
              <li key={p.post_id} className="border-t border-line pt-2 first:border-0 first:pt-0">
                <p className="line-clamp-2">{p.text}</p>
                <p className="text-xs text-muted">
                  {p.source} · {p.date} · ER {pct(p.er)}{p.overperformance ? ` · ×${p.overperformance.toFixed(1)} к медиане` : ""}
                  {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="ml-1 text-accent">открыть</a>}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
