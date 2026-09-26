"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { ApiError } from "@/lib/api";
import { claimPendingAudit } from "@/lib/audit";
import { useAuth } from "@/lib/auth";

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await register({
        email: String(f.get("email")),
        password: String(f.get("password")),
        full_name: String(f.get("full_name") || "") || undefined,
        organization_name: String(f.get("organization_name")),
      });
      const claimed = await claimPendingAudit(); // бесплатный аудит до регистрации → в организацию
      router.push(claimed ? `/audit/${claimed}` : "/dashboard");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 422 ? "Проверьте email и пароль (от 8 символов)" :
        err instanceof Error ? err.message : "Не удалось зарегистрироваться");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Регистрация" subtitle="Бесплатный тариф: 1 аудит контента и 3 генерации">
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="label" htmlFor="organization_name">Компания</label>
          <input id="organization_name" name="organization_name" required className="input" />
        </div>
        <div>
          <label className="label" htmlFor="full_name">Имя</label>
          <input id="full_name" name="full_name" className="input" autoComplete="name" />
        </div>
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" name="email" type="email" required className="input" autoComplete="email" />
        </div>
        <div>
          <label className="label" htmlFor="password">Пароль</label>
          <input id="password" name="password" type="password" required minLength={8} className="input"
                 autoComplete="new-password" />
        </div>
        {error && <p className="text-sm text-bad">{error}</p>}
        <button className="btn w-full" disabled={busy}>{busy ? "Создаём…" : "Создать аккаунт"}</button>
        <p className="text-center text-sm text-muted">
          Уже есть аккаунт? <Link href="/login" className="font-semibold text-accent">Войти</Link>
        </p>
      </form>
    </AuthCard>
  );
}
