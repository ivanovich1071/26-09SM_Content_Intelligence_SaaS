"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { DayBars, Kpi } from "@/components/AdminUi";
import { PageHeader } from "@/components/ComingSoon";
import { usd, type AdminOverview } from "@/lib/admin";
import { api } from "@/lib/api";

export default function AdminHome() {
  const [d, setD] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<AdminOverview>("/admin/overview").then(setD).catch((e) => setError(e.message));
  }, []);
  if (!d) return <p className="text-sm text-muted">{error ?? "Загрузка…"}</p>;

  const delta = d.ai.cost_prev_month ? Math.round(((d.ai.cost_month - d.ai.cost_prev_month) / d.ai.cost_prev_month) * 100) : null;
  const errRate = d.ai.calls_24h ? Math.round((d.ai.errors_24h / d.ai.calls_24h) * 100) : 0;
  return (
    <>
      <PageHeader title="Админка" subtitle="Состояние сервиса: клиенты, тарифы, расход модели и фоновые задачи." />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Организации" value={d.organizations.total} hint={`+${d.organizations.new_7d} за 7 дней · платных ${d.organizations.paying}`} />
        <Kpi label="Пользователи" value={d.users.total}
             hint={`заходили за 7 дней: ${d.users.seen_7d} · заблокировано ${d.users.total - d.users.active}`} />
        <Kpi label="MRR по тарифам" value={usd(d.mrr_usd)}
             hint={d.unpriced_paying ? `ещё ${d.unpriced_paying} платных без цены в тарифе` : "по ценам тарифов"} />
        <Kpi label="Расход AI за месяц" value={usd(d.ai.cost_month)}
             hint={`прошлый месяц ${usd(d.ai.cost_prev_month)}${delta === null ? "" : ` (${delta > 0 ? "+" : ""}${delta}%)`}`} />
        <Kpi label="Вызовов модели за месяц" value={d.ai.calls_month.toLocaleString("ru-RU")}
             hint={`ошибок за 24 ч: ${d.ai.errors_24h} (${errRate}%)`} tone={errRate >= 10 ? "text-bad" : undefined} />
        <Kpi label="Задачи в работе" value={d.jobs.active} hint={`создано за 24 ч: ${d.jobs.created_24h}`} />
        <Kpi label="Упали за 24 ч" value={<Link href="/admin/errors" className="hover:underline">{d.jobs.failed_24h}</Link>}
             tone={d.jobs.failed_24h ? "text-bad" : "text-good"} />
        <Kpi label="Зависли в очереди" value={<Link href="/admin/jobs?status=stuck" className="hover:underline">{d.jobs.stuck}</Link>}
             hint="queued дольше 15 минут — проверьте воркер" tone={d.jobs.stuck ? "text-warn" : "text-good"} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="card">
          <h2 className="mb-3 font-semibold">Расход модели по дням, 30 дней</h2>
          <DayBars days={d.ai.by_day} />
        </div>
        <div className="card">
          <h2 className="mb-2 font-semibold">Тарифы</h2>
          <ul className="space-y-1 text-sm">
            {Object.entries(d.organizations.by_plan).sort((a, b) => b[1] - a[1]).map(([plan, n]) => (
              <li key={plan} className="flex justify-between">
                <Link href={`/admin/organizations?plan=${plan}`} className="hover:text-accent">{plan}</Link><b>{n}</b>
              </li>
            ))}
          </ul>
          {d.organizations.system > 0 && <p className="mt-2 text-xs text-muted">Служебных организаций: {d.organizations.system} (публичные аудиты)</p>}
          <h2 className="mt-4 mb-2 font-semibold">Больше всего тратят в этом месяце</h2>
          {d.top_orgs.length === 0 ? <p className="text-sm text-muted">Вызовов ещё не было.</p> : (
            <ul className="space-y-1 text-sm">
              {d.top_orgs.map((o) => (
                <li key={String(o.id)} className="flex justify-between gap-2">
                  {o.id ? <Link href={`/admin/organizations/${o.id}`} className="truncate hover:text-accent">{o.name}</Link>
                    : <span className="text-muted">без организации</span>}
                  <span>{usd(o.cost_usd)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}
