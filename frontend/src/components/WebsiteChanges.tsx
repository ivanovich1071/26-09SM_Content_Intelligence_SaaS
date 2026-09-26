"use client";

import { ChevronDown, ChevronRight, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type WebsiteChange, type WebsiteChangeDetail } from "@/lib/api";
import { fmtDate } from "@/lib/format";

export const PAGE_KINDS: Record<string, string> = {
  home: "главная", pricing: "цены", service: "услуги", about: "о компании", blog: "блог", article: "статья",
  contacts: "контакты", other: "страница",
};
const CATEGORY: Record<string, string> = {
  price: "цены", offer: "оффер", product: "продукт", positioning: "позиционирование", contacts: "контакты",
  content: "контент", other: "другое",
};
const IMPORTANCE: Record<string, { label: string; cls: string }> = {
  high: { label: "важно", cls: "bg-bad/10 text-bad" },
  medium: { label: "заметно", cls: "bg-warn/10 text-warn" },
  low: { label: "мелочь", cls: "bg-bg text-muted" },
};
const KIND: Record<WebsiteChange["kind"], string> = { changed: "изменена", new_page: "новая страница", removed_page: "удалена" };

function Diff({ id }: { id: number }) {
  const [d, setD] = useState<WebsiteChangeDetail | null>(null);
  useEffect(() => {
    api<WebsiteChangeDetail>(`/websites/changes/${id}`).then(setD).catch(() => null);
  }, [id]);
  if (!d) return <p className="text-xs text-muted">Загрузка…</p>;
  if (d.kind === "new_page") {
    return <p className="line-clamp-6 text-xs whitespace-pre-line text-muted">{d.after_text?.slice(0, 1200)}</p>;
  }
  if (d.kind === "removed_page") return <p className="text-xs text-muted">Страница больше не открывается (404).</p>;
  return (
    <div className="grid gap-2 text-xs md:grid-cols-2">
      <div>
        <p className="label">Было</p>
        <ul className="space-y-1">{d.removed.map((l, i) => <li key={i} className="rounded bg-bad/10 px-2 py-1 line-through decoration-bad/40">{l}</li>)}</ul>
        {d.removed.length === 0 && <p className="text-muted">—</p>}
      </div>
      <div>
        <p className="label">Стало</p>
        <ul className="space-y-1">{d.added.map((l, i) => <li key={i} className="rounded bg-good/10 px-2 py-1">{l}</li>)}</ul>
        {d.added.length === 0 && <p className="text-muted">—</p>}
      </div>
    </div>
  );
}

/** Лента изменений сайтов: в «Сайтах» и в таймлайне конкурента. */
export function WebsiteChanges({ changes, showSite = true }: { changes: WebsiteChange[]; showSite?: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  if (changes.length === 0) return <p className="text-sm text-muted">Изменений пока нет — первый обход только запоминает страницы.</p>;
  return (
    <ul className="space-y-3">
      {changes.map((c) => {
        const imp = c.importance ? IMPORTANCE[c.importance] : null;
        return (
          <li key={c.id} className="text-sm">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              <span>{fmtDate(c.detected_at)}</span>
              {showSite && <span className="font-medium text-ink">{c.competitor_name ?? c.website}</span>}
              <a href={c.page_url} target="_blank" rel="noreferrer" className="hover:text-accent">
                {PAGE_KINDS[c.page_kind] ?? c.page_kind}: {c.page_title ?? c.page_url}
              </a>
              <span>{KIND[c.kind]}</span>
              {c.category && <span>{CATEGORY[c.category] ?? c.category}</span>}
              {imp && <span className={`rounded-md px-1.5 py-0.5 ${imp.cls}`}>{imp.label}</span>}
            </div>
            <button className="mt-1 flex items-start gap-1 text-left hover:text-accent" onClick={() => setOpen(open === c.id ? null : c.id)}>
              {open === c.id ? <ChevronDown className="mt-0.5 size-4 shrink-0" /> : <ChevronRight className="mt-0.5 size-4 shrink-0" />}
              <span>
                {c.ai && <Sparkles className="mr-1 inline size-3.5 text-accent" />}
                {c.summary ?? `+${c.added_count} / −${c.removed_count} строк`}
              </span>
            </button>
            {open === c.id && <div className="mt-2 pl-5"><Diff id={c.id} /></div>}
          </li>
        );
      })}
    </ul>
  );
}
