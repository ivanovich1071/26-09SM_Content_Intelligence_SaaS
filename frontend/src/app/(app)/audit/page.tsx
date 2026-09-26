"use client";

import { Loader2, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Audit, type AuditBrief } from "@/lib/api";
import { STAGES, scoreTone } from "@/lib/audit";
import { useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/format";

const STATUS: Record<AuditBrief["status"], string> = {
  queued: "в очереди", running: "идёт", completed: "готов", failed: "ошибка",
};

export default function AuditPage() {
  const { org } = useAuth();
  const router = useRouter();
  const canManage = org?.role !== "viewer";
  const [audits, setAudits] = useState<AuditBrief[] | null>(null);
  const [form, setForm] = useState({ company: "", website: "", sources: "", own: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<AuditBrief[]>("/audits").then(setAudits).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);
  const active = audits?.some((a) => a.status === "queued" || a.status === "running") ?? false;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [active, load]);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const a = await api<Audit>("/audits", { method: "POST", json: {
        company: form.company || null, website: form.website || null,
        sources: form.sources.split(/[\s,]+/).filter(Boolean), use_own_sources: form.own } });
      router.push(`/audit/${a.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
      setBusy(false);
    }
  }

  async function remove(a: AuditBrief) {
    if (!confirm(`Удалить аудит «${a.company}»?`)) return;
    await api(`/audits/${a.id}`, { method: "DELETE" }).catch((e) => setError(e.message));
    load();
  }

  return (
    <>
      <PageHeader title="Аудит контента"
                  subtitle="Сайт и каналы компании → сбор → разметка → метрики → сравнение с вашим рынком → оценка по 6 критериям. Общий балл считает код, модель объясняет." />
      {canManage && (
        <form onSubmit={start} className="card mb-4 grid gap-3 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="company">Компания</label>
            <input id="company" className="input" value={form.company} placeholder={org?.name}
                   onChange={(e) => setForm({ ...form, company: e.target.value })} />
          </div>
          <div>
            <label className="label" htmlFor="site">Сайт</label>
            <input id="site" className="input" value={form.website} placeholder="company.by"
                   onChange={(e) => setForm({ ...form, website: e.target.value })} />
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="src">Каналы — Telegram, VK, YouTube, Instagram, RSS (через пробел или с новой строки)</label>
            <textarea id="src" rows={2} className="input" value={form.sources} placeholder="https://t.me/channel  https://vk.com/company"
                      onChange={(e) => setForm({ ...form, sources: e.target.value })} />
            <p className="mt-1 text-xs text-muted">Если каналы не указаны — возьмём соцсети, найденные на сайте.</p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.own} onChange={(e) => setForm({ ...form, own: e.target.checked })} />
            Добавить мои источники с ролью «свой»
          </label>
          <div className="flex justify-end">
            <button className="btn" disabled={busy}>{busy && <Loader2 className="size-4 animate-spin" />}Провести аудит</button>
          </div>
          {error && <p className="text-sm text-bad md:col-span-2">{error}</p>}
        </form>
      )}

      <div className="card">
        <h2 className="mb-2 font-semibold">Аудиты</h2>
        {audits === null ? <p className="text-sm text-muted">Загрузка…</p> : audits.length === 0 ? (
          <p className="text-sm text-muted">Аудитов пока нет. Укажите сайт или канал — первый отчёт будет через несколько минут.</p>
        ) : (
          <ul className="divide-y divide-line">
            {audits.map((a) => (
              <li key={a.id} className="flex items-center gap-4 py-2">
                <span className={`w-12 text-2xl font-extrabold ${scoreTone(a.score, 100)}`}>{a.score === null ? "—" : Math.round(a.score)}</span>
                <Link href={`/audit/${a.id}`} className="min-w-0 flex-1 hover:text-accent">
                  <p className="font-semibold">{a.company}</p>
                  <p className="truncate text-xs text-muted">{a.website ?? ""} {fmtDate(a.created_at)}</p>
                </Link>
                <span className={`text-sm ${a.status === "failed" ? "text-bad" : a.status === "completed" ? "text-muted" : ""}`}>
                  {a.status === "running" ? STAGES.find((s) => s.key === a.stage)?.label ?? STATUS.running : STATUS[a.status]}
                </span>
                {canManage && (
                  <button className="btn-ghost text-bad" title="Удалить" onClick={() => remove(a)}><Trash2 className="size-4" /></button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
