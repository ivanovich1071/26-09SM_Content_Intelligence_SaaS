"use client";

import { CalendarClock, Loader2, Mail, Sparkles } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type DigestList, type DigestSchedule } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RUNNING } from "@/lib/factory";
import { fmtDate } from "@/lib/format";

const WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];

function Schedule({ canEdit }: { canEdit: boolean }) {
  const [s, setS] = useState<DigestSchedule | null>(null);
  const [recipients, setRecipients] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    api<DigestSchedule>("/digests/schedule").then((d) => { setS(d); setRecipients(d.recipients.join(", ")); }).catch(() => null);
  }, []);
  if (!s) return null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      const d = await api<DigestSchedule>("/digests/schedule", { method: "PUT", json: {
        ...s, recipients: recipients.split(/[\s,;]+/).filter(Boolean) } });
      setS(d);
      setMsg({ ok: true, text: "Сохранено" });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Ошибка" });
    }
  }

  const set = (patch: Partial<DigestSchedule>) => setS({ ...s, ...patch });
  return (
    <form onSubmit={save} className="card space-y-3">
      <h2 className="flex items-center gap-2 font-semibold"><CalendarClock className="size-4" />Расписание</h2>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" disabled={!canEdit} checked={s.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        Собирать автоматически
      </label>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <div className="w-40">
          <label className="label" htmlFor="period">Как часто</label>
          <select id="period" className="input" disabled={!canEdit} value={s.period} onChange={(e) => set({ period: e.target.value as DigestSchedule["period"] })}>
            <option value="weekly">Раз в неделю</option><option value="monthly">Раз в месяц</option><option value="custom">Раз в N дней</option>
          </select>
        </div>
        {s.period === "weekly" && (
          <div className="w-40">
            <label className="label" htmlFor="wd">День</label>
            <select id="wd" className="input" disabled={!canEdit} value={s.weekday} onChange={(e) => set({ weekday: Number(e.target.value) })}>
              {WEEKDAYS.map((w, i) => <option key={w} value={i}>{w}</option>)}
            </select>
          </div>
        )}
        {s.period === "monthly" && (
          <div className="w-24">
            <label className="label" htmlFor="day">Число</label>
            <input id="day" type="number" min={1} max={28} className="input" disabled={!canEdit} value={s.day} onChange={(e) => set({ day: Number(e.target.value) })} />
          </div>
        )}
        {s.period === "custom" && (
          <div className="w-24">
            <label className="label" htmlFor="every">Дней</label>
            <input id="every" type="number" min={1} max={90} className="input" disabled={!canEdit} value={s.every_days} onChange={(e) => set({ every_days: Number(e.target.value) })} />
          </div>
        )}
        <div className="w-28">
          <label className="label" htmlFor="hour">Час (UTC)</label>
          <input id="hour" type="number" min={0} max={23} className="input" disabled={!canEdit} value={s.hour} onChange={(e) => set({ hour: Number(e.target.value) })} />
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" disabled={!canEdit || !s.email_configured} checked={s.send_email} onChange={(e) => set({ send_email: e.target.checked })} />
        <Mail className="size-4" />Отправлять на email
      </label>
      {!s.email_configured && <p className="text-xs text-muted">Email не настроен на сервере (SMTP_HOST, SMTP_FROM в .env) — дайджест доступен здесь, в .md/.html и PDF.</p>}
      {s.send_email && (
        <div>
          <label className="label" htmlFor="rcpt">Получатели</label>
          <input id="rcpt" className="input" disabled={!canEdit} value={recipients} placeholder="ceo@company.by, marketing@company.by"
                 onChange={(e) => setRecipients(e.target.value)} />
        </div>
      )}
      {s.next_run_at && s.enabled && <p className="text-xs text-muted">Следующий дайджест: {fmtDate(s.next_run_at)}</p>}
      {canEdit ? (
        <div className="flex items-center gap-3">
          <button className="btn">Сохранить</button>
          {msg && <span className={`text-sm ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</span>}
        </div>
      ) : <p className="text-xs text-muted">Менять расписание могут администраторы.</p>}
    </form>
  );
}

export default function DigestPage() {
  const { org } = useAuth();
  const canManage = org?.role !== "viewer";
  const canEdit = org?.role === "owner" || org?.role === "admin";
  const [data, setData] = useState<DigestList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<DigestList>("/digests").then(setData).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);
  const running = !!data?.job && RUNNING.has(data.job.status);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running, load]);

  async function generate(days: number) {
    setError(null);
    try {
      await api("/digests/generate", { method: "POST", json: { days } });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  const job = data?.job;
  return (
    <>
      <PageHeader title="Дайджест"
                  subtitle="Что произошло на рынке за период: темы, конкуренты и их сайты, лучшие и необычные публикации, пробелы, ваш контент и что делать дальше. Цифры считает код, выводы — модель." />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          {canManage && (
            <div className="card flex flex-wrap items-center gap-3">
              <button className="btn" disabled={running} onClick={() => generate(7)}>Собрать за неделю</button>
              <button className="btn-ghost" disabled={running} onClick={() => generate(30)}>За 30 дней</button>
              {running && <span className="flex items-center gap-1 text-sm"><Loader2 className="size-4 animate-spin" />Собираем… {job?.progress ?? 0}%</span>}
              {job?.status === "failed" && <span className="text-sm text-bad">{job.error}</span>}
              {job?.status === "completed" && typeof job.result?.message === "string" && <span className="text-sm text-warn">{job.result.message}</span>}
            </div>
          )}
          {error && <p className="text-sm text-bad">{error}</p>}
          <div className="card">
            <h2 className="mb-2 font-semibold">Выпуски</h2>
            {data === null ? <p className="text-sm text-muted">Загрузка…</p> : data.items.length === 0 ? (
              <p className="text-sm text-muted">Дайджестов пока нет. Соберите первый или включите расписание.</p>
            ) : (
              <ul className="divide-y divide-line">
                {data.items.map((d) => (
                  <li key={d.id}>
                    <Link href={`/digest/${d.id}`} className="block py-2 hover:text-accent">
                      <p className="font-medium">{d.ai && <Sparkles className="mr-1 inline size-3.5 text-accent" />}{d.headline}</p>
                      <p className="text-xs text-muted">
                        {d.title}{d.trigger === "schedule" ? " · по расписанию" : ""}
                        {d.emailed_at && " · отправлен на email"}{d.email_error && " · письмо не ушло"}
                      </p>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        <Schedule canEdit={canEdit} />
      </div>
    </>
  );
}
