"use client";

import { Circle } from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Job } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const ONBOARDING = [
  { title: "Заполнить профиль компании и brand voice", href: "/settings/company" },
  { title: "Подключить свои источники (сайт, Telegram)", href: "/settings/sources" },
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

export default function Dashboard() {
  const { org } = useAuth();
  return (
    <>
      <PageHeader title="Обзор" subtitle={`Организация «${org?.name}». Аналитика рынка появится после подключения источников.`} />
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
