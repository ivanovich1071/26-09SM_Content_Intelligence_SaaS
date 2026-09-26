"use client";

import { Loader2, Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { PostList } from "@/components/PostList";
import { api, type Competitor, type FeedPage, type Post, type Taxonomy } from "@/lib/api";
import { label, media } from "@/lib/format";

type Filters = {
  q: string;
  search: "auto" | "text" | "semantic";
  role: string;
  competitor_id: string;
  topic: string;
  media_type: string;
  funnel_stage: string;
  content_type: string;
  has_cta: string;
  days: string;
  min_overperformance: string;
  sort: string;
};

const EMPTY: Filters = {
  q: "", search: "auto", role: "", competitor_id: "", topic: "", media_type: "", funnel_stage: "", content_type: "",
  has_cta: "", days: "", min_overperformance: "", sort: "recent",
};
const MEDIA = ["text", "photo", "album", "video", "reel", "carousel", "document", "poll"];

function toQuery(f: Filters): string {
  const p = new URLSearchParams();
  for (const k of ["q", "role", "competitor_id", "topic", "media_type", "funnel_stage", "content_type", "has_cta",
                   "min_overperformance", "sort"] as const) {
    if (f[k]) p.set(k, f[k]);
  }
  if (f.q) p.set("search", f.search);
  if (f.days) p.set("date_from", new Date(Date.now() - Number(f.days) * 86400000).toISOString());
  return p.toString();
}

function Select({ value, onChange, children, title }: {
  value: string; onChange: (v: string) => void; children: React.ReactNode; title: string;
}) {
  return (
    <label className="text-xs">
      <span className="label">{title}</span>
      <select className="input w-44" value={value} onChange={(e) => onChange(e.target.value)}>{children}</select>
    </label>
  );
}

export default function FeedPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [draftQ, setDraftQ] = useState("");
  const [items, setItems] = useState<Post[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ mode: string | null; notice: string | null }>({ mode: null, notice: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tax, setTax] = useState<Taxonomy | null>(null);
  const [competitors, setCompetitors] = useState<Competitor[]>([]);

  useEffect(() => {
    api<Taxonomy>("/taxonomy").then(setTax).catch(() => null);
    api<Competitor[]>("/competitors").then(setCompetitors).catch(() => null);
  }, []);

  const query = useMemo(() => toQuery(filters), [filters]);
  const load = useCallback(async (after: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const page = await api<FeedPage>(`/posts?${query}&limit=30${after ? `&cursor=${encodeURIComponent(after)}` : ""}`);
      setItems((prev) => (after && prev ? [...prev, ...page.items] : page.items));
      setCursor(page.next_cursor);
      setMeta({ mode: page.search_mode, notice: page.notice });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
    setLoading(false);
  }, [query]);
  useEffect(() => { load(null); }, [load]);

  const set = (k: keyof Filters) => (v: string) => setFilters((f) => ({ ...f, [k]: v }));
  const active = JSON.stringify({ ...filters, sort: "recent" }) !== JSON.stringify(EMPTY);
  const topics = tax ? [...tax.topics, "другое"] : [];

  return (
    <>
      <PageHeader title="Лента"
                  subtitle="Публикации ваших источников, конкурентов и рынка. Нажмите на пост — метрики против медианы канала, похожие посты и AI-разбор." />

      <div className="card mb-4 space-y-3">
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); set("q")(draftQ.trim()); }}>
          <div className="relative min-w-72 flex-1">
            <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted" />
            <input className="input" style={{ paddingLeft: "2.25rem" }} value={draftQ} onChange={(e) => setDraftQ(e.target.value)}
                   placeholder="Поиск: «как конкуренты говорят о доставке», «скидка»…" />
          </div>
          <div className="w-44">
            <select className="input" value={filters.search} onChange={(e) => set("search")(e.target.value)}>
              <option value="auto">По смыслу</option>
              <option value="text">По словам</option>
            </select>
          </div>
          <button className="btn">Найти</button>
        </form>

        <div className="flex flex-wrap items-end gap-3">
          <Select title="Чьи" value={filters.role} onChange={set("role")}>
            <option value="">Все</option><option value="own">Ваши</option>
            <option value="competitor">Конкуренты</option><option value="market">Рынок</option>
          </Select>
          <Select title="Конкурент" value={filters.competitor_id} onChange={set("competitor_id")}>
            <option value="">Все</option>
            {competitors.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          <Select title="Тема" value={filters.topic} onChange={set("topic")}>
            <option value="">Все</option>{topics.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
          <Select title="Формат" value={filters.media_type} onChange={set("media_type")}>
            <option value="">Все</option>{MEDIA.map((m) => <option key={m} value={m}>{media(m)}</option>)}
          </Select>
          <Select title="Тип" value={filters.content_type} onChange={set("content_type")}>
            <option value="">Все</option>
            {(tax?.universal.content_type ?? []).map((v) => <option key={v} value={v}>{label(v)}</option>)}
          </Select>
          <Select title="Воронка" value={filters.funnel_stage} onChange={set("funnel_stage")}>
            <option value="">Все</option>
            {(tax?.universal.funnel_stage ?? []).map((v) => <option key={v} value={v}>{label(v)}</option>)}
          </Select>
          <Select title="Призыв" value={filters.has_cta} onChange={set("has_cta")}>
            <option value="">Неважно</option><option value="true">Есть CTA</option><option value="false">Без CTA</option>
          </Select>
          <Select title="Период" value={filters.days} onChange={set("days")}>
            <option value="">Всё время</option><option value="7">7 дней</option>
            <option value="30">30 дней</option><option value="90">90 дней</option>
          </Select>
          <Select title="Отклик" value={filters.min_overperformance} onChange={set("min_overperformance")}>
            <option value="">Любой</option><option value="1.5">от ×1,5</option>
            <option value="2">от ×2</option><option value="3">от ×3</option>
          </Select>
          <Select title="Сортировка" value={filters.sort} onChange={set("sort")}>
            <option value="recent">Свежие</option><option value="top">Лучшие (×)</option><option value="er">По ER</option>
            {filters.q && <option value="relevance">Релевантность</option>}
          </Select>
          {active && (
            <button className="btn-ghost" onClick={() => { setFilters(EMPTY); setDraftQ(""); }}>
              <X className="size-4" />Сбросить
            </button>
          )}
        </div>
        {filters.q && meta.mode && (
          <p className="text-xs text-muted">
            {meta.mode === "semantic" ? "Поиск по смыслу" : "Поиск по словам"}: «{filters.q}»
            {meta.notice && <span className="text-warn"> — {meta.notice}</span>}
          </p>
        )}
      </div>

      {error && <p className="mb-4 text-sm text-bad">{error}</p>}
      <div className="card">
        {items === null ? <p className="text-sm text-muted">Загрузка…</p> : (
          <>
            <PostList posts={items} linked />
            {cursor && (
              <button className="btn-ghost mt-4 w-full" onClick={() => load(cursor)} disabled={loading}>
                {loading && <Loader2 className="size-4 animate-spin" />}Показать ещё
              </button>
            )}
          </>
        )}
      </div>
    </>
  );
}
