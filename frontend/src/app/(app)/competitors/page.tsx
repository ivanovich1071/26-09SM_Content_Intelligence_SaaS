"use client";

import { Loader2, Plus, Search, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Competitor, type Discovery } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtNum, KIND_ICON, KIND_LABELS, pct } from "@/lib/format";

function AddCompetitor({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [found, setFound] = useState<Discovery | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState<"discover" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function discover() {
    setError(null);
    setBusy("discover");
    try {
      const d = await api<Discovery>("/competitors/discover", { method: "POST", json: { website } });
      setFound(d);
      setPicked(new Set(d.social_links.filter((l) => l.supported && !l.needs_key).map((l) => l.url)));
      if (!name && d.title) setName(d.title);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
    setBusy(null);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy("save");
    try {
      const sources = [...picked, ...extra.split(/[\s,]+/).filter(Boolean)];
      await api("/competitors", { method: "POST", json: { name, website: website || null, sources } });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
    }
    setBusy(null);
  }

  function toggle(url: string) {
    const next = new Set(picked);
    if (next.has(url)) next.delete(url);
    else next.add(url);
    setPicked(next);
  }

  return (
    <form onSubmit={save} className="card mb-4 space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-60 flex-1">
          <label className="label" htmlFor="website">Сайт конкурента</label>
          <input id="website" className="input" value={website} onChange={(e) => setWebsite(e.target.value)}
                 placeholder="competitor.by" />
        </div>
        <button type="button" className="btn-ghost" onClick={discover} disabled={!website || busy !== null}>
          {busy === "discover" ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
          Найти соцсети на сайте
        </button>
        <div className="min-w-60 flex-1">
          <label className="label" htmlFor="name">Название</label>
          <input id="name" required className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
      </div>

      {found && (
        <div className="rounded-xl bg-bg p-3 text-sm">
          <p className="font-medium">На сайте найдено:</p>
          {found.social_links.length === 0 && <p className="text-muted">Ссылок на соцсети нет — добавьте каналы вручную ниже.</p>}
          <ul className="mt-2 space-y-1">
            {found.social_links.map((l) => {
              const Icon = l.kind ? KIND_ICON[l.kind] : null;
              return (
                <li key={l.url}>
                  <label className={`flex items-center gap-2 ${l.supported ? "" : "text-muted"}`}>
                    <input type="checkbox" disabled={!l.supported} checked={picked.has(l.url)} onChange={() => toggle(l.url)} />
                    {Icon && <Icon className="size-4 text-accent" />}
                    <span>{l.url}</span>
                    {!l.supported && <span className="text-xs">— площадка пока не поддерживается</span>}
                    {l.needs_key && <span className="text-xs text-warn">— нужен ключ API на сервере</span>}
                  </label>
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-xs text-muted">
            Сам сайт тоже подключится: {found.feed_url ? `статьи блога через ${found.feed_url}` : "RSS блога не найден — статьи собираться не будут"}.
          </p>
        </div>
      )}

      <div>
        <label className="label" htmlFor="extra">Другие каналы (через пробел или запятую)</label>
        <input id="extra" className="input" value={extra} onChange={(e) => setExtra(e.target.value)}
               placeholder="t.me/канал  instagram.com/профиль  youtube.com/@канал  vk.com/сообщество" />
      </div>
      <div className="flex items-center gap-3">
        <button className="btn" disabled={busy !== null || !name}>
          {busy === "save" && <Loader2 className="size-4 animate-spin" />}Добавить конкурента
        </button>
        <span className="text-xs text-muted">Каналы учитываются в лимите источников тарифа.</span>
      </div>
      {error && <p className="text-sm text-bad">{error}</p>}
    </form>
  );
}

export default function CompetitorsPage() {
  const { org } = useAuth();
  const canManage = org?.role !== "viewer";
  const [items, setItems] = useState<Competitor[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Competitor[]>("/competitors").then(setItems).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title="Конкуренты"
                    subtitle="Каналы конкурентов собираются и размечаются автоматически; AI-профиль строится по их контенту. Данные за 30 дней." />
        {canManage && (
          <button className="btn" onClick={() => setAdding(!adding)}>
            {adding ? <X className="size-4" /> : <Plus className="size-4" />}{adding ? "Отмена" : "Добавить"}
          </button>
        )}
      </div>
      {adding && <AddCompetitor onDone={() => { setAdding(false); load(); }} />}
      {error && <p className="mb-4 text-sm text-bad">{error}</p>}

      {items === null ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : items.length === 0 ? (
        <div className="card text-sm text-muted">
          Конкурентов пока нет. Добавьте сайт конкурента — сервис сам найдёт его соцсети.
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {items.map((c) => (
            <Link key={c.id} href={`/competitors/${c.id}`} className="card block transition-all duration-200 hover:border-accent">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-semibold">{c.name}</p>
                  {c.website && <p className="text-xs text-muted">{c.website}</p>}
                </div>
                <div className="flex gap-1.5">
                  {c.sources.map((s) => {
                    const Icon = KIND_ICON[s.kind];
                    return <span key={s.id} title={`${KIND_LABELS[s.kind]}: ${s.status}`}><Icon className={`size-4 ${s.status === "ok" ? "text-accent" : "text-muted"}`} /></span>;
                  })}
                </div>
              </div>
              <p className="mt-3 line-clamp-2 text-sm">
                {c.profile?.ai.summary ?? <span className="text-muted">AI-профиль появится после сбора и разметки постов.</span>}
              </p>
              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted">
                <span>Постов: {fmtNum(c.stats.posts)}</span>
                <span>В неделю: {fmtNum(c.stats.posts_per_week)}</span>
                <span>Медиана ER: {pct(c.stats.median_er)}</span>
                {c.stats.topics[0] && <span>Главная тема: {c.stats.topics[0].value}</span>}
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
