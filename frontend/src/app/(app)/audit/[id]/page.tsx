"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AuditProgress, AuditReport } from "@/components/AuditReport";
import { api, type Audit } from "@/lib/api";

export default function AuditView() {
  const { id } = useParams<{ id: string }>();
  const [audit, setAudit] = useState<Audit | null>(null);
  const [error, setError] = useState<string | null>(null);

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
        <AuditReport audit={audit} />
      ) : (
        <>
          <h1 className="mb-3 text-2xl font-bold">Аудит: {audit.company}</h1>
          <AuditProgress status={audit.status} stage={audit.stage} progress={audit.progress} error={audit.error} />
        </>
      )}
    </>
  );
}
