"use client";

import { useEffect, useState } from "react";
import { Flag } from "@/components/AdminUi";
import { PageHeader } from "@/components/ComingSoon";
import { usd, type AdminProviders } from "@/lib/admin";
import { api } from "@/lib/api";
import { fmtDate, KIND_LABELS } from "@/lib/format";
import type { SourceKind } from "@/lib/api";

export default function ProvidersPage() {
  const [d, setD] = useState<AdminProviders | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<AdminProviders>("/admin/providers").then(setD).catch((e) => setError(e.message));
  }, []);
  if (!d) return <p className="text-sm text-muted">{error ?? "Загрузка…"}</p>;

  return (
    <>
      <PageHeader title="Провайдеры" subtitle="Ключи и модели задаются только в .env на сервере — здесь видно, что настроено и как работает. Значения ключей не показываются." />
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card space-y-1 text-sm">
          <h2 className="font-semibold">Инфраструктура</h2>
          <p className="flex justify-between">Redis <Flag ok={d.infra.redis === "ok"} yes="ok" no={d.infra.redis} /></p>
          <p className="flex justify-between">Очередь у API <Flag ok={d.infra.queue} yes="подключена" no="нет — задачи остаются queued" /></p>
          <p className="flex justify-between">Воркер <Flag ok={d.infra.worker} yes="отмечался за час" no="нет отметки" /></p>
        </div>
        <div className="card space-y-1 text-sm">
          <h2 className="font-semibold">Email (дайджест)</h2>
          <p className="flex justify-between">SMTP <Flag ok={d.email.configured} yes="настроен" no="нет (SMTP_HOST, SMTP_FROM)" /></p>
          {d.email.host && <p className="text-muted">{d.email.host} · {d.email.from}</p>}
          <h2 className="pt-3 font-semibold">Капча публичного аудита</h2>
          <p className="flex justify-between">Turnstile <Flag ok={d.captcha.configured} yes="включена" no="выключена" /></p>
        </div>
        <div className="card space-y-1 text-sm">
          <h2 className="font-semibold">Модель: {d.llm.provider}</h2>
          <p className="flex justify-between gap-2">OPENROUTER_API_KEY <Flag ok={d.llm.configured} yes="задан" no="не задан — работают шаблоны" /></p>
          {Object.entries(d.llm.models).map(([task, model]) => (
            <p key={task} className="flex justify-between gap-2"><span className="text-muted">{task}</span><span className="truncate">{model}</span></p>
          ))}
          <p className="flex justify-between gap-2"><span className="text-muted">embeddings</span><span className="truncate">{d.llm.embedding.model} ({d.llm.embedding.dim})</span></p>
        </div>
      </div>

      <div className="card mt-4 overflow-x-auto">
        <h2 className="font-semibold">Модели за 24 часа</h2>
        {d.llm.health_24h.length === 0 ? <p className="mt-2 text-sm text-muted">Вызовов не было.</p> : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr><th className="py-1">Модель</th><th>Вызовов</th><th>Ошибок</th><th>Медиана задержки</th><th>Стоимость</th><th>Последний успешный</th></tr>
            </thead>
            <tbody>
              {d.llm.health_24h.map((m) => (
                <tr key={m.provider + m.model} className="border-t border-line">
                  <td className="py-1.5">{m.model} <span className="text-xs text-muted">{m.provider}</span></td><td>{m.calls}</td>
                  <td className={m.error_rate >= 0.1 ? "font-semibold text-bad" : "text-muted"}>{m.errors} ({Math.round(m.error_rate * 100)}%)</td>
                  <td>{m.p50_latency_ms === null ? "—" : `${(m.p50_latency_ms / 1000).toFixed(1)} с`}</td>
                  <td>{usd(m.cost_usd)}</td><td className="text-xs">{fmtDate(m.last_ok_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card mt-4 overflow-x-auto">
        <h2 className="font-semibold">Коннекторы источников</h2>
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr><th className="py-1">Площадка</th><th>Ключ</th><th>Источников</th><th>В ошибке</th><th>Недоступны</th><th>Последний сбор</th></tr>
          </thead>
          <tbody>
            {d.connectors.map((c) => (
              <tr key={c.kind} className="border-t border-line">
                <td className="py-1.5">{KIND_LABELS[c.kind as SourceKind] ?? c.kind}</td>
                <td>{c.key_setting ? <Flag ok={c.configured} yes={c.key_setting} no={`нет ${c.key_setting}`} /> : <span className="text-muted">не нужен</span>}</td>
                <td>{c.sources}</td>
                <td className={c.errors ? "text-bad" : "text-muted"}>{c.errors}</td>
                <td className={c.unavailable ? "text-warn" : "text-muted"}>{c.unavailable}</td>
                <td className="text-xs">{fmtDate(c.last_synced_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
