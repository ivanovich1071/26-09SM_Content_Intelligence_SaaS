"use client";

import { ArrowLeft, Lightbulb } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AuditProgress, AuditReport } from "@/components/AuditReport";
import { api, ApiError, type Audit } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function AuditView() {
  const { id } = useParams<{ id: string }>();
  const [audit, setAudit] = useState<Audit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { org } = useAuth();
  const router = useRouter();

  async function toStrategy() {
    try {
      await api("/strategy/opportunities/generate", { method: "POST", json: { audit_id: Number(id) } });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) return setError(e instanceof Error ? e.message : "Ошибка");
    }
    router.push("/strategy");
  }

  const load = useCallback(() => {
    api<Audit>(`/audits/${id}`).then(setAudit).catch((e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);
  const running = audit?.status === "queued" || audit?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running, load]);

  return (
    <>
      <Link href="/audit" className="no-print mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-accent">
        <ArrowLeft className="size-4" />Все аудиты
      </Link>
      {error && <p className="text-sm text-bad">{error}</p>}
      {!audit ? !error && <p className="text-sm text-muted">Загрузка…</p> : audit.status === "completed" ? (
        <>
          <AuditReport audit={audit} />
          {org?.role !== "viewer" && (
            <div className="card no-print mt-4 flex flex-wrap items-center gap-4">
              <Lightbulb className="size-6 text-accent" />
              <p className="min-w-0 flex-1 text-sm">
                <b>Что писать дальше.</b> 10 тем, где рынок пишет, а вы нет, — с учётом слабых мест этого аудита.
              </p>
              <button className="btn" onClick={toStrategy}>Подобрать темы</button>
            </div>
          )}
        </>
      ) : (
        <>
          <h1 className="mb-3 text-2xl font-bold">Аудит: {audit.company}</h1>
          <AuditProgress status={audit.status} stage={audit.stage} progress={audit.progress} error={audit.error} />
        </>
      )}
    </>
  );
}
