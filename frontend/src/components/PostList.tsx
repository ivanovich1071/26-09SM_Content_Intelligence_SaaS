"use client";

import type { Post } from "@/lib/api";
import { fmtDate, fmtNum, label, media, pct, times } from "@/lib/format";

/** Список постов с метриками и разметкой — в источниках, карточке конкурента, позже в Ленте. */
export function PostList({ posts }: { posts: Post[] }) {
  if (posts.length === 0) return <p className="text-sm text-muted">Постов пока нет.</p>;
  return (
    <ul className="space-y-3">
      {posts.map((p) => (
        <li key={p.id} className="text-sm">
          <div className="flex flex-wrap gap-x-3 text-xs text-muted">
            <span>{fmtDate(p.published_at)}</span>
            <span>{media(p.media_type)}</span>
            {p.views !== null && <span>просмотры {fmtNum(p.views)}</span>}
            {p.likes !== null && <span>реакции {fmtNum(p.likes)}</span>}
            {p.er !== null && <span>ER {pct(p.er)}</span>}
            {p.overperformance !== null && (
              <span className={p.overperformance >= 1.5 ? "font-semibold text-good" : ""}
                    title="Во сколько раз лучше медианы источника">{times(p.overperformance)}</span>
            )}
            {p.duplicate_of_id !== null && <span className="text-warn">дубль</span>}
            {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">открыть</a>}
          </div>
          {p.title && <p className="font-medium">{p.title}</p>}
          <p className="line-clamp-3 whitespace-pre-line">{p.text || "(без текста)"}</p>
          {p.analysis && !p.analysis.error && (
            <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
              {[p.analysis.topic, label(p.analysis.content_type), label(p.analysis.funnel_stage),
                p.analysis.hook_type !== "нет" ? `хук: ${label(p.analysis.hook_type)}` : null,
                p.analysis.cta_type !== "нет" ? `CTA: ${label(p.analysis.cta_type)}` : null,
                p.analysis.target_role].filter(Boolean).map((t, i) => (
                <span key={i} className={`rounded-md px-1.5 py-0.5 ${i === 0 ? "bg-accent-soft text-accent" : "bg-bg text-muted"}`}>{t}</span>
              ))}
            </div>
          )}
          {p.analysis?.summary && <p className="mt-1 text-xs text-muted">{p.analysis.summary}</p>}
        </li>
      ))}
    </ul>
  );
}
