import type { ContentProjectBrief } from "@/lib/api";

export const STATUS_NAMES: Record<ContentProjectBrief["status"], string> = {
  draft: "черновик", approved: "согласован", published: "опубликован", archived: "в архиве",
};
export const QA_TONE: Record<string, string> = { ok: "text-good", warn: "text-warn", error: "text-bad" };
export const QA_NAMES: Record<string, string> = { ok: "QA пройден", warn: "есть замечания", error: "нужны правки" };
export const RUNNING = new Set(["queued", "running", "analyzing"]);
