"use client";

import { Loader2, Lock } from "lucide-react";
import Link from "next/link";
import Script from "next/script";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { AuditProgress, AuditReport } from "@/components/AuditReport";
import { api, type Audit } from "@/lib/api";
import { rememberPublicAudit } from "@/lib/audit";

const SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;

declare global {
  interface Window { onTurnstile?: (token: string) => void }
}

function Cta({ token }: { token: string }) {
  return (
    <div className="card no-print flex flex-wrap items-center gap-4 border-accent bg-accent-soft">
      <Lock className="size-6 text-accent" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">Полный отчёт — после бесплатной регистрации</p>
        <p className="text-sm text-muted">
          Пояснения и советы по всем критериям, все проблемы, сравнение с вашими конкурентами и темы, где рынок пишет, а вы нет.
        </p>
      </div>
      <Link href="/register" className="btn" onClick={() => rememberPublicAudit(token)}>Открыть полный отчёт</Link>
      <Link href="/login" className="text-sm text-accent" onClick={() => rememberPublicAudit(token)}>Уже есть аккаунт</Link>
    </div>
  );
}

function Form() {
  const router = useRouter();
  const [form, setForm] = useState({ company: "", website: "", sources: "", email: "", hp: "" });
  const [captcha, setCaptcha] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    window.onTurnstile = setCaptcha;
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const a = await api<Audit>("/public/audits", { method: "POST", json: {
        company: form.company, website: form.website || null, sources: form.sources.split(/[\s,]+/).filter(Boolean),
        email: form.email || null, captcha_token: captcha, hp: form.hp || null } });
      router.replace(`/free-audit?t=${a.token}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card mt-8 grid gap-3 md:grid-cols-2">
      <div>
        <label className="label" htmlFor="company">Компания</label>
        <input id="company" required className="input" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} />
      </div>
      <div>
        <label className="label" htmlFor="site">Сайт</label>
        <input id="site" className="input" placeholder="company.by" value={form.website} onChange={(e) => setForm({ ...form, website: e.target.value })} />
      </div>
      <div className="md:col-span-2">
        <label className="label" htmlFor="src">Telegram, VK, YouTube — через пробел</label>
        <input id="src" className="input" placeholder="https://t.me/channel" value={form.sources} onChange={(e) => setForm({ ...form, sources: e.target.value })} />
        <p className="mt-1 text-xs text-muted">Можно указать только сайт — соцсети найдём на нём сами.</p>
      </div>
      <div>
        <label className="label" htmlFor="email">Email (необязательно)</label>
        <input id="email" type="email" className="input" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
      </div>
      {/* ловушка для ботов: человек это поле не видит */}
      <input tabIndex={-1} autoComplete="off" aria-hidden className="hidden" value={form.hp} onChange={(e) => setForm({ ...form, hp: e.target.value })} />
      <div className="flex items-end justify-end gap-3">
        {SITE_KEY && (
          <>
            <Script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer />
            <div className="cf-turnstile" data-sitekey={SITE_KEY} data-callback="onTurnstile" />
          </>
        )}
        <button className="btn" disabled={busy || (!!SITE_KEY && !captcha)}>
          {busy && <Loader2 className="size-4 animate-spin" />}Провести аудит
        </button>
      </div>
      {error && <p className="text-sm text-bad md:col-span-2">{error}</p>}
      <p className="text-xs text-muted md:col-span-2">Один бесплатный аудит в сутки. Анализируем только публичные данные.</p>
    </form>
  );
}

function Result({ token }: { token: string }) {
  const [audit, setAudit] = useState<Audit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Audit>(`/public/audits/${token}`).then(setAudit).catch((e) => setError(e.message));
  }, [token]);
  useEffect(load, [load]);
  const running = audit?.status === "queued" || audit?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running, load]);

  if (error) return <p className="mt-8 text-sm text-bad">{error}</p>;
  if (!audit) return <p className="mt-8 text-sm text-muted">Загрузка…</p>;
  if (audit.status !== "completed") {
    return (
      <div className="mt-8">
        <h2 className="mb-3 text-xl font-bold">Аудит: {audit.company}</h2>
        <AuditProgress status={audit.status} stage={audit.stage} progress={audit.progress} error={audit.error} />
        <p className="mt-2 text-sm text-muted">Обычно это 2–5 минут. Страницу можно не обновлять.</p>
      </div>
    );
  }
  return <div className="mt-8"><AuditReport audit={audit} lockedCta={<Cta token={token} />} /></div>;
}

function FreeAudit() {
  const token = useSearchParams().get("t");
  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <Link href="/" className="text-sm font-extrabold">SM Content Intelligence</Link>
      <h1 className="mt-6 text-3xl font-extrabold">Бесплатный аудит контента</h1>
      <p className="mt-2 max-w-2xl text-muted">
        Оценим сайт и каналы компании по 6 критериям: стратегия, попадание в аудиторию, цепляющее начало, польза,
        отличие от конкурентов и призыв к действию. С доказательствами из ваших постов.
      </p>
      {token ? <Result token={token} /> : <Form />}
    </main>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="p-8 text-sm text-muted">Загрузка…</p>}><FreeAudit /></Suspense>;
}
