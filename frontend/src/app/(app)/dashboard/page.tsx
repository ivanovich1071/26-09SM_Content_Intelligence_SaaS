"use client";

import { Circle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Job, type MarketOverview, type SourceRole } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const ONBOARDING = [
  { title: "Заполнить brand voice", href: "/settings/brand-voice" },
  { title: "Подключить свои источники (сайт, Telegram)", href: "/settings/sources" },
  { title: "Настроить темы и аудиторию ниши", href: "/settings/company" },
  { title: "Добавить конкурентов", href: "/competitors" },
  { title: "Провести аудит контента", href: "/audit" },
  { title: "Сгенерировать первый материал", href: "/factory" },
];

const FINAL = new Set(["completed", "failed", "cancelled"]);

function SystemCheck() {
  const { org } = useAuth();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canRun = org?.role !== "viewer";

  const poll = useCallback(async (id: number) => {
    for (let i = 0; i < 30; i++) {
      const j = await api<Job>(`/jobs/${id}`);
      setJob(j);
      if (FINAL.has(j.status)) return;
      await new Promise((r) => setTimeout(r, 1000));
    }
  }, []);

  async function run() {
    setError(null);
    try {
      const j = await api<Job>("/jobs/ping", { method: "POST" });
      setJob(j);
      await poll(j.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  return (
    <div className="card">
      <h2 className="font-semibold">Проверка фоновых задач</h2>
      <p className="mt-1 text-sm text-muted">
        Сбор, аудит и генерация идут в фоне. Тестовая задача проходит через очередь и воркер.
      </p>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-ghost" onClick={run} disabled={!canRun}>Запустить проверку</button>
        {job && (
          <span className={`text-sm ${job.status === "completed" ? "text-good" : job.status === "failed" ? "text-bad" : "text-muted"}`}>
            Задача #{job.id}: {job.status}
            {job.status === "queued" && " (если долго — воркер не запущен)"}
          </span>
        )}
        {error && <span className="text-sm text-bad">{error}</span>}
      </div>
    </div>
  );
}

const PERIODS = [7, 30, 90];
const ROLE_TITLES: Record<SourceRole, string> = { own: "Вы", competitor: "Конкуренты", market: "Рынок" };

function Market() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<MarketOverview | null>(null);
  useEffect(() => {
    api<MarketOverview>(`/market/overview?days=${days}`).then(setData).catch(() => setData(null));
  }, [days]);
  if (!data) return null;
  const total = Object.values(data.by_role).reduce((n, r) => n + r.posts, 0);
  const maxTopic = Math.max(1, ...data.topics.map((t) => t.total));
  return (
    <div className="mb-4 space-y-4">
      <div className="flex items-center gap-2">
        {PERIODS.map((d) => (
          <button key={d} onClick={() => setDays(d)}
                  className={`rounded-lg px-3 py-1 text-sm ${d === days ? "bg-accent text-white" : "bg-surface text-muted hover:text-ink"}`}>
            {d} дней
          </button>
        ))}
      </div>
      {total === 0 ? (
        <div className="card text-sm text-muted">
          За {days} дней публикаций нет. <Link href="/settings/sources" className="text-accent hover:underline">Добавьте источники</Link> — свои, конкурентов и рынка.
        </div>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            {(Object.keys(ROLE_TITLES) as SourceRole[]).map((role) => {
              const r = data.by_role[role];
              return (
                <div key={role} className="card">
                  <p className="label">{ROLE_TITLES[role]}</p>
                  <p className="text-2xl font-bold">{r.posts_per_week.toLocaleString("ru-RU")}<span className="text-base font-medium text-muted"> постов/нед</span></p>
                  <p className="mt-1 text-sm text-muted">
                    Постов: {r.posts} · размечено: {r.analyzed} · медиана ER: {r.median_er === null ? "—" : `${r.median_er}%`}
                  </p>
                </div>
              );
            })}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="card">
              <h2 className="font-semibold">Темы</h2>
              {data.topics.length === 0 ? (
                <p className="mt-2 text-sm text-muted">Посты ещё не размечены — анализ запускается после сбора (нужен OPENROUTER_API_KEY).</p>
              ) : (
                <ul className="mt-3 space-y-2 text-sm">
                  {data.topics.slice(0, 10).map((t) => (
                    <li key={t.topic}>
                      <div className="flex justify-between gap-2"><span>{t.topic}</span><span className="text-muted">вы {t.own} · конк. {t.competitor} · рынок {t.market}</span></div>
                      <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-bg">
                        <div className="bg-accent" style={{ width: `${(100 * t.own) / maxTopic}%` }} />
                        <div className="bg-warn" style={{ width: `${(100 * t.competitor) / maxTopic}%` }} />
                        <div className="bg-muted" style={{ width: `${(100 * t.market) / maxTopic}%` }} />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="card">
              <h2 className="font-semibold">Лучшие публикации конкурентов и рынка</h2>
              <p className="text-xs text-muted">Во сколько раз пост лучше медианы своего источника</p>
              {data.top_posts.length === 0 ? (
                <p className="mt-2 text-sm text-muted">Пока нет данных — нужно минимум 3 поста с метриками в источнике.</p>
              ) : (
                <ul className="mt-3 space-y-3 text-sm">
                  {data.top_posts.map((p) => (
                    <li key={p.id}>
                      <div className="flex flex-wrap gap-x-3 text-xs text-muted">
                        <span className="font-semibold text-good">×{p.overperformance.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}</span>
                        <span>{p.source}</span>
                        {p.topic && <span>{p.topic}</span>}
                        {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">открыть</a>}
                      </div>
                      <p className="line-clamp-2">{p.summary || p.text}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { org } = useAuth();
  return (
    <>
      <PageHeader title="Обзор" subtitle={`Организация «${org?.name}». Рынок по вашим источникам: объём, вовлечённость, темы и лучшие публикации.`} />
      <Market />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card">
          <h2 className="font-semibold">С чего начать</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {ONBOARDING.map((s) => (
              <li key={s.href}>
                <Link href={s.href} className="flex items-center gap-2 hover:text-accent">
                  <Circle className="size-4 text-muted" />
                  {s.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
        <SystemCheck />
      </div>
    </>
  );
}
