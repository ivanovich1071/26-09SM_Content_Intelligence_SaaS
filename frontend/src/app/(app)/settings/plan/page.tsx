"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Plan, type Usage } from "@/lib/api";
import { METRIC_LABELS } from "@/lib/nav";

export default function PlanPage() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    api<Plan[]>("/billing/plans").then(setPlans);
    api<Usage>("/billing/usage").then((u) => setCurrent(u.plan));
  }, []);

  return (
    <>
      <PageHeader
        title="Тариф"
        subtitle="Онлайн-оплата подключается после замера себестоимости. Чтобы сменить тариф, напишите нам — его назначит администратор."
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {plans.map((p) => (
          <div key={p.code} className={`card ${p.code === current ? "border-accent ring-2 ring-accent-soft" : ""}`}>
            <p className="font-bold">{p.name}</p>
            <p className="text-sm text-muted">
              {p.price_month_usd === null ? "цена уточняется" : p.price_month_usd === 0 ? "бесплатно" : `$${p.price_month_usd}/мес`}
            </p>
            {p.code === current && <p className="mt-1 text-xs font-semibold text-accent">Ваш тариф</p>}
            <ul className="mt-3 space-y-1 text-sm">
              {Object.entries(p.limits).map(([k, v]) => (
                <li key={k} className="flex justify-between gap-2">
                  <span className="text-muted">{METRIC_LABELS[k] ?? k}</span>
                  <span className="font-medium">{v === null ? "∞" : v}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </>
  );
}
