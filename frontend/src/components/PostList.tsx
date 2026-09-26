"use client";

import Link from "next/link";
import type { Post, SourceRole } from "@/lib/api";
import { fmtDate, fmtNum, KIND_ICON, label, media, pct, times } from "@/lib/format";

const ROLE_TAG: Record<SourceRole, string> = { own: "вы", competitor: "конкурент", market: "рынок" };

/** Список постов с метриками и разметкой — в источниках, карточке конкурента, позже в Ленте. */
export function PostList({ posts, linked = false }: { posts: Post[]; linked?: boolean }) {
  if (posts.length === 0) return <p className="text-sm text-muted">Постов пока нет.</p>;
  return (
    <ul className="space-y-3">
      {posts.map((p) => (
        <li key={p.id} className="text-sm">
          <div className="flex flex-wrap gap-x-3 text-xs text-muted">
            {p.source && <SourceTag post={p} />}
            <span>{fmtDate(p.published_at)}</span>
            <span>{media(p.media_type)}</span>
            {p.views !== null && <span>просмотры {fmtNum(p.views)}</span>}
            {p.likes !== null && <span>реакции {fmtNum(p.likes)}</span>}
            {p.er !== null && <span>ER {pct(p.er)}</span>}
            {p.overperformance !== null && (
              <span className={p.overperformance >= 1.5 ? "font-semibold text-good" : ""}
                    title="Во сколько раз лучше медианы источника">{times(p.overperformance)}</span>
            )}
            {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">открыть</a>}
          </div>
          {linked ? (
            <Link href={`/feed/${p.id}`} className="block hover:text-accent">
              {p.title && <p className="font-medium">{p.title}</p>}
              <p className="line-clamp-3 whitespace-pre-line">{p.text || "(без текста)"}</p>
            </Link>
          ) : (
            <>
              {p.title && <p className="font-medium">{p.title}</p>}
              <p className="line-clamp-3 whitespace-pre-line">{p.text || "(без текста)"}</p>
            </>
          )}
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

export function SourceTag({ post }: { post: Post }) {
  if (!post.source) return null;
  const Icon = KIND_ICON[post.source.kind];
  return (
    <span className="inline-flex items-center gap-1 font-medium text-ink">
      <Icon className="size-3.5 text-accent" />
      {post.source.competitor_name ?? post.source.name}
      <span className="font-normal text-muted">· {ROLE_TAG[post.source.role]}</span>
    </span>
  );
}
