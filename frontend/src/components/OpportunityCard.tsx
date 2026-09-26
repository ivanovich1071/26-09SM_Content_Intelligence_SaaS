"use client";

import { ExternalLink, Factory, Sparkles, X } from "lucide-react";
import { ShareBars } from "@/components/ShareBars";
import type { Opportunity, OpportunityStatus } from "@/lib/api";
import { CRITERIA_NAMES, FORMAT_NAMES } from "@/lib/audit";
import { label, pct } from "@/lib/format";

/** Тема из Content Strategy: формулировка модели + цифры и примеры рынка, посчитанные кодом. */
export function OpportunityCard({ o, onStatus }: { o: Opportunity; onStatus?: (s: OpportunityStatus) => void }) {
  const m = o.market;
  return (
    <div className="card">
      <div className="flex items-start gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-soft font-bold text-accent">{o.rank}</span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold">{o.ai && <Sparkles className="mr-1 inline size-4 text-accent" />}{o.title}</h3>
          <div className="mt-1 flex flex-wrap gap-1.5 text-xs">
            {o.topic && <span className="rounded-md bg-bg px-1.5 py-0.5">{o.topic}</span>}
            {o.formats.map((f) => <span key={f} className="rounded-md bg-accent-soft px-1.5 py-0.5 text-accent">{FORMAT_NAMES[f] ?? f}</span>)}
            {o.funnel_stage && <span className="rounded-md bg-bg px-1.5 py-0.5 text-muted">воронка: {label(o.funnel_stage)}</span>}
            {o.target_role && <span className="rounded-md bg-bg px-1.5 py-0.5 text-muted">для: {o.target_role}</span>}
            {o.status === "in_factory" && <span className="rounded-md bg-good/10 px-1.5 py-0.5 text-good">в Контент Заводе</span>}
          </div>
        </div>
        {onStatus && (
          <div className="flex shrink-0 gap-2">
            {o.status === "new" && <>
              <button className="btn" onClick={() => onStatus("in_factory")}><Factory className="size-4" />В Контент Завод</button>
              <button className="btn-ghost" title="Не подходит" onClick={() => onStatus("dismissed")}><X className="size-4" /></button>
            </>}
            {o.status === "in_factory" && <>
              <button className="btn-ghost" onClick={() => onStatus("done")}>Опубликовано</button>
              <button className="btn-ghost" onClick={() => onStatus("new")}>Вернуть</button>
            </>}
            {(o.status === "dismissed" || o.status === "done") && <button className="btn-ghost" onClick={() => onStatus("new")}>Вернуть</button>}
          </div>
        )}
      </div>

      {o.why && <p className="mt-3 text-sm">{o.why}</p>}
      {o.angle && <p className="mt-1 text-sm text-muted"><b className="font-semibold text-ink">Угол:</b> {o.angle}</p>}
      {o.fixes.length > 0 && (
        <p className="mt-1 text-xs text-muted">Подтянет по аудиту: {o.fixes.map((f) => CRITERIA_NAMES[f] ?? f).join(", ")}</p>
      )}

      <div className="mt-3 grid gap-4 border-t border-line pt-3 md:grid-cols-2">
        <div>
          <p className="label">Рынок и вы</p>
          <ShareBars own={m.share_own} market={m.share_market} />
          <ul className="mt-2 space-y-0.5 text-xs text-muted">
            <li>Пробел: <b className={m.gap >= 5 ? "text-warn" : "text-ink"}>{m.gap > 0 ? "+" : ""}{m.gap} п.п.</b> · публикаций рынка: {m.market_total}, ваших: {m.own}</li>
            {m.trend_pp !== null && <li>Доля у рынка к прошлому периоду: {m.trend_pp > 0 ? "+" : ""}{m.trend_pp} п.п.</li>}
            <li>ER темы {pct(m.median_er)} при медиане рынка {pct(m.market_median_er)} · {m.saturation_per_week} публ./нед.</li>
            {m.competitors.length > 0 && <li>Пишут: {m.competitors.join(", ")}</li>}
          </ul>
        </div>
        <div>
          <p className="label">Лучшее у рынка</p>
          {o.examples.length === 0 ? <p className="text-xs text-muted">Нет примеров.</p> : (
            <ul className="space-y-2 text-xs">
              {o.examples.map((e) => (
                <li key={e.post_id}>
                  <p className="line-clamp-2">{e.text}</p>
                  <p className="text-muted">
                    {e.source}{e.date ? ` · ${e.date}` : ""}{e.overperformance ? ` · ×${e.overperformance.toFixed(1)} к медиане` : ""}
                    {e.url && <a href={e.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-0.5 text-accent">открыть<ExternalLink className="size-3" /></a>}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
