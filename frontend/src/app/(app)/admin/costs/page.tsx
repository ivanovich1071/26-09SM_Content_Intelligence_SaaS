"use client";

import { useEffect, useState } from "react";
import { CostTable, DayBars, Kpi } from "@/components/AdminUi";
import { PageHeader } from "@/components/ComingSoon";
import { usd, type AdminCosts } from "@/lib/admin";
import { api } from "@/lib/api";

export default function CostsPage() {
  const [days, setDays] = useState(30);
  const [d, setD] = useState<AdminCosts | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<AdminCosts>(`/admin/costs?days=${days}`).then(setD).catch((e) => setError(e.message));
  }, [days]);

  return (
    <>
      <PageHeader title="Расходы AI" subtitle="Все вызовы модели по журналу llm_requests: кто, на что и сколько тратит. Себестоимость клиента — основа для цен тарифов." />
      <div className="mb-4 flex gap-2">
        {[7, 30, 90].map((n) => (
          <button key={n} className={n === days ? "btn" : "btn-ghost"} onClick={() => setDays(n)}>{n} дней</button>
        ))}
      </div>
      {error && <p className="text-sm text-bad">{error}</p>}
      {d && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi label="Стоимость" value={usd(d.totals.cost_usd)} hint={`в среднем ${usd(d.totals.cost_usd / d.days)} в день`} />
            <Kpi label="Вызовов" value={d.totals.calls.toLocaleString("ru-RU")}
                 hint={`ошибок ${d.totals.errors} (${d.totals.calls ? Math.round((d.totals.errors / d.totals.calls) * 100) : 0}%)`}
                 tone={d.totals.calls && d.totals.errors / d.totals.calls >= 0.1 ? "text-bad" : undefined} />
            <Kpi label="Токены вход / выход" value={`${(d.totals.input_tokens / 1000).toFixed(0)}k / ${(d.totals.output_tokens / 1000).toFixed(0)}k`} />
            <Kpi label="Средняя задержка" value={d.totals.avg_latency_ms === null ? "—" : `${(d.totals.avg_latency_ms / 1000).toFixed(1)} с`} />
          </div>
          <div className="card mt-4"><h2 className="mb-3 font-semibold">По дням</h2><DayBars days={d.by_day} /></div>
          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            <CostTable title="Организации" keyName="Организация" rows={d.by_org} />
            <CostTable title="Модели" keyName="Модель" rows={d.by_model} />
            <CostTable title="Задачи AI Router" keyName="Задача" rows={d.by_task} />
            <CostTable title="Операции" keyName="Операция" rows={d.by_operation} />
          </div>
        </>
      )}
    </>
  );
}
