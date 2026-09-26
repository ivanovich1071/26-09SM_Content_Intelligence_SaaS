"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Usage } from "@/lib/api";
import { METRIC_LABELS } from "@/lib/nav";

// Метрики расхода за месяц: ключ лимита → ключ в usage_events
const MONTHLY: [string, string][] = [
  ["ai_cost_usd_month", "ai_cost_usd"],
  ["audits_month", "audits"],
  ["generations_month", "generations"],
];

function fmt(n: number, money: boolean) {
  return money ? `$${n.toFixed(n < 1 ? 3 : 2)}` : String(Math.round(n));
}

export default function UsagePage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Usage>("/billing/usage").then(setUsage).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="text-sm text-bad">{error}</p>;
  if (!usage) return <p className="text-sm text-muted">Загрузка…</p>;

  const since = new Date(usage.period_start).toLocaleDateString("ru-RU");
  return (
    <>
      <PageHeader title="Использование" subtitle={`Тариф ${usage.plan}. Расход с ${since}.`} />
      <div className="grid gap-4 md:grid-cols-3">
        {MONTHLY.map(([limitKey, usedKey]) => {
          const used = usage.used[usedKey] ?? 0;
          const limit = usage.limits[limitKey];
          const money = usedKey === "ai_cost_usd";
          const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
          return (
            <div key={limitKey} className="card">
              <p className="label">{METRIC_LABELS[limitKey]}</p>
              <p className="text-2xl font-bold">
                {fmt(used, money)}
                <span className="text-base font-medium text-muted"> / {limit === null ? "∞" : fmt(limit, money)}</span>
              </p>
              {limit !== null && (
                <div className="mt-3 h-2 rounded-full bg-bg">
                  <div
                    className={`h-2 rounded-full ${pct >= 90 ? "bg-bad" : pct >= 70 ? "bg-warn" : "bg-accent"}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="card mt-4">
        <h2 className="font-semibold">Вызовы AI по операциям</h2>
        {usage.ai_by_operation.length === 0 ? (
          <p className="mt-2 text-sm text-muted">В этом месяце вызовов модели ещё не было.</p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead className="text-left text-muted">
              <tr><th className="py-1">Операция</th><th>Вызовов</th><th>Стоимость</th></tr>
            </thead>
            <tbody>
              {usage.ai_by_operation.map((r) => (
                <tr key={r.operation} className="border-t border-line">
                  <td className="py-1.5">{r.operation}</td><td>{r.calls}</td><td>${r.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
