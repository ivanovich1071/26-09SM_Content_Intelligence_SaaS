import { api, type Audit, type AuditStage } from "@/lib/api";

export const STAGES: { key: AuditStage; label: string }[] = [
  { key: "sources", label: "Источники" },
  { key: "collect", label: "Сбор публикаций" },
  { key: "classify", label: "Разметка" },
  { key: "metrics", label: "Метрики и рынок" },
  { key: "audit", label: "Оценка критериев" },
];

/** Цвет балла: 0–10 для критерия, 0–100 для общего. */
export function scoreTone(score: number | null, max = 10): string {
  if (score === null) return "text-muted";
  const s = score / max;
  return s >= 0.7 ? "text-good" : s >= 0.45 ? "text-warn" : "text-bad";
}

const PENDING = "sm.pendingAudit";

/** Публичный аудит ждёт регистрации: токен живёт в localStorage до входа. */
export function rememberPublicAudit(token: string) {
  try {
    localStorage.setItem(PENDING, token);
  } catch {
    /* без хранилища просто не перенесём */
  }
}

/** После входа/регистрации: переносит бесплатный аудит в организацию → id аудита или null. */
export async function claimPendingAudit(): Promise<number | null> {
  let token: string | null = null;
  try {
    token = localStorage.getItem(PENDING);
    localStorage.removeItem(PENDING);
  } catch {
    return null;
  }
  if (!token) return null;
  try {
    return (await api<Audit>("/audits/claim", { method: "POST", json: { token } })).id;
  } catch {
    return null; // уже перенесён или истёк — не мешаем входу
  }
}
