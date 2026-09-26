"use client";

import { ArrowLeft, Check, Copy, Download, ExternalLink, Loader2, RefreshCw, ShieldCheck, Sparkles, Wand2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, download, type ContentFormat, type ContentProject, type ContentVersion, type QACheck } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { QA_NAMES, QA_TONE, RUNNING, STATUS_NAMES } from "@/lib/factory";
import { fmtDate } from "@/lib/format";

const KIND: Record<ContentVersion["kind"], string> = { write: "черновик", edit: "правка редактора", manual: "ручная правка" };
const CHECK_NAMES: Record<string, string> = {
  length: "длина", placeholders: "шаблоны", cta: "призыв", facts: "факты", similarity: "близость к рынку",
  tone: "тон", structure: "структура", clarity: "ясность",
};
const LEVEL_TONE: Record<string, string> = { ok: "text-good", warn: "text-warn", error: "text-bad" };

function asText(fmt: string, f: Record<string, string>): string {
  if (fmt === "email") return `Тема: ${f.subject ?? ""}\nПрехедер: ${f.preheader ?? ""}\n\n${f.text ?? ""}`;
  if (fmt === "article") return `${f.title ?? ""}\n\n${f.lead ?? ""}\n\n${f.text ?? ""}`;
  return f.text ?? "";
}

function Checks({ checks }: { checks: QACheck[] }) {
  const shown = checks.filter((c) => c.level !== "ok");
  if (shown.length === 0) return <p className="flex items-center gap-1 text-sm text-good"><Check className="size-4" />Замечаний нет</p>;
  return (
    <ul className="space-y-2 text-sm">
      {shown.map((c, i) => (
        <li key={i} className="flex gap-2">
          <span className={`mt-1.5 size-2 shrink-0 rounded-full ${c.level === "error" ? "bg-bad" : "bg-warn"}`} />
          <span>
            <span className={`text-xs font-semibold ${LEVEL_TONE[c.level]}`}>{CHECK_NAMES[c.code] ?? c.code}</span>
            {c.source === "ai" && <Sparkles className="ml-1 inline size-3 text-accent" />}
            <br />{c.message}
            {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-0.5 text-accent">пост<ExternalLink className="size-3" /></a>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function FactoryProject() {
  const { id } = useParams<{ id: string }>();
  const { org } = useAuth();
  const canManage = org?.role !== "viewer";
  const [project, setProject] = useState<ContentProject | null>(null);
  const [formats, setFormats] = useState<ContentFormat[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async (select?: "latest") => {
    try {
      const p = await api<ContentProject>(`/factory/projects/${id}`);
      setProject(p);
      setSelected((cur) => {
        const next = select === "latest" || cur === null || !p.items.some((v) => v.number === cur) ? p.items[0]?.number ?? null : cur;
        const v = p.items.find((x) => x.number === next);
        if (v && (select === "latest" || next !== cur)) setDraft(v.fields);
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { api<ContentFormat[]>("/factory/formats").then(setFormats).catch(() => null); }, []);

  const job = project?.job;
  const running = !!job && RUNNING.has(job.status);
  const [wasRunning, setWasRunning] = useState(false);
  useEffect(() => {
    if (!running) {
      if (wasRunning) { setWasRunning(false); void load("latest"); }
      return;
    }
    setWasRunning(true);
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [running, wasRunning, load]);

  const version = project?.items.find((v) => v.number === selected) ?? null;
  const spec = formats.find((f) => f.key === project?.format);
  const dirty = useMemo(() => !!version && JSON.stringify(draft) !== JSON.stringify(version.fields), [draft, version]);

  async function act(name: string, fn: () => Promise<unknown>, reload: "latest" | undefined = undefined) {
    setBusy(name);
    setError(null);
    try {
      await fn();
      await load(reload);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
    setBusy(null);
  }
  const generate = (action: string, extra: object = {}) =>
    act(action, () => api(`/factory/projects/${id}/generate`, { method: "POST", json: { action, ...extra } }));

  if (!project) return <p className="text-sm text-muted">{error ?? "Загрузка…"}</p>;
  const qa = version?.qa;
  const ctx = version?.context ?? {};
  return (
    <>
      <Link href="/factory" className="mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-accent"><ArrowLeft className="size-4" />Контент Завод</Link>
      <div className="mb-4 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold">{project.title}</h1>
          <p className="text-sm text-muted">{spec?.label ?? project.format}{project.brief && ` · ${project.brief}`}</p>
        </div>
        <div className="w-44">
          <select className="input" disabled={!canManage} value={project.status}
                  onChange={(e) => act("status", () => api(`/factory/projects/${id}`, { method: "PATCH", json: { status: e.target.value } }))}>
            {Object.entries(STATUS_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
      </div>
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      {running && <p className="mb-3 flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin" />
        {job?.progress && job.progress < 75 ? "Собираем контекст и пишем…" : "Проверяем качество…"} {job?.progress ?? 0}%</p>}
      {job?.status === "failed" && !running && <p className="mb-3 text-sm text-bad">{job.error}</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="card space-y-3">
          {project.items.length === 0 ? (
            <div className="text-sm text-muted">
              {running ? "Черновик пишется…" : "Черновика нет."}
              {canManage && !running && (
                <div className="mt-3 flex gap-2">
                  <button className="btn" onClick={() => generate("write")}><Wand2 className="size-4" />Написать черновик</button>
                  <button className="btn-ghost" onClick={() => {
                    // пустая «версия 0» — только в браузере, на сервер уйдёт при сохранении
                    const empty = Object.fromEntries((spec?.fields ?? []).map((f) => [f.key, ""]));
                    setProject({ ...project, items: [{ id: 0, number: 0, kind: "manual", instruction: null, fields: empty,
                                                       model: null, created_at: "", qa: {}, context: {} }] });
                    setSelected(0);
                    setDraft(empty);
                  }}>
                    Написать вручную
                  </button>
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <select className="input w-auto" value={selected ?? ""} onChange={(e) => {
                  const v = project.items.find((x) => x.number === Number(e.target.value));
                  setSelected(Number(e.target.value));
                  if (v) setDraft(v.fields);
                }}>
                  {project.items.filter((v) => v.number > 0).map((v) => (
                    <option key={v.number} value={v.number}>Версия {v.number} — {KIND[v.kind]} · {fmtDate(v.created_at)}</option>
                  ))}
                </select>
                {version?.instruction && <span className="text-xs text-muted">«{version.instruction}»</span>}
              </div>
              {(spec?.fields ?? []).map((f) => {
                const n = (draft[f.key] ?? "").trim().length;
                return (
                  <div key={f.key}>
                    <div className="flex justify-between">
                      <label className="label" htmlFor={f.key}>{f.label}</label>
                      <span className={`text-xs ${n > f.max ? "text-bad" : n < f.min ? "text-warn" : "text-muted"}`}>{n} / {f.min}–{f.max}</span>
                    </div>
                    {f.multiline ? (
                      <textarea id={f.key} className="input font-[inherit] leading-relaxed" rows={f.key === "text" ? (project.format === "article" ? 24 : 14) : 3}
                                disabled={!canManage} value={draft[f.key] ?? ""} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
                    ) : (
                      <input id={f.key} className="input" disabled={!canManage} value={draft[f.key] ?? ""}
                             onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
                    )}
                  </div>
                );
              })}
              <div className="flex flex-wrap gap-2">
                {canManage && (
                  <button className="btn" disabled={(!dirty && version?.number !== 0) || !!busy}
                          onClick={() => act("save", () => api(`/factory/projects/${id}/versions`, { method: "POST", json: { fields: draft } }), "latest")}>
                    {busy === "save" && <Loader2 className="size-4 animate-spin" />}Сохранить как новую версию
                  </button>
                )}
                <button className="btn-ghost" onClick={async () => {
                  await navigator.clipboard.writeText(asText(project.format, draft));
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                }}>{copied ? <Check className="size-4" /> : <Copy className="size-4" />}Копировать</button>
                {version && version.number > 0 && (
                  <>
                    <button className="btn-ghost" onClick={() => download(`/factory/projects/${id}/export?type=md&version=${version.number}`, `content-${id}-v${version.number}.md`).catch((e) => setError(e.message))}>
                      <Download className="size-4" />.md
                    </button>
                    <button className="btn-ghost" onClick={() => download(`/factory/projects/${id}/export?type=html&version=${version.number}`, `content-${id}-v${version.number}.html`).catch((e) => setError(e.message))}>
                      <Download className="size-4" />.html{project.format === "email" ? " (письмо)" : ""}
                    </button>
                  </>
                )}
              </div>
              {dirty && <p className="text-xs text-warn">Есть несохранённые правки</p>}
              {canManage && version && version.number > 0 && (
                <div className="border-t border-line pt-3">
                  <label className="label" htmlFor="instr">Попросить редактора</label>
                  <div className="flex gap-2">
                    <input id="instr" className="input" value={instruction} placeholder="Короче на треть, добавь вопрос к читателю, убери эмодзи…"
                           onChange={(e) => setInstruction(e.target.value)} />
                    <button className="btn shrink-0" disabled={running || !instruction.trim() || !!busy}
                            onClick={() => generate("edit", { instruction }).then(() => setInstruction(""))}>
                      <Wand2 className="size-4" />Править
                    </button>
                  </div>
                  <button className="mt-2 text-xs text-muted hover:text-accent" disabled={running} onClick={() => generate("write")}>
                    <RefreshCw className="mr-1 inline size-3" />Написать заново с нуля
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        <div className="space-y-4">
          <div className="card">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h2 className="font-semibold">Проверка качества</h2>
              {qa?.status && <span className={`text-sm ${QA_TONE[qa.status]}`}>{QA_NAMES[qa.status]}</span>}
            </div>
            {qa?.checks ? <Checks checks={qa.checks} /> : <p className="text-sm text-muted">Появится после первой версии.</p>}
            {qa?.checks && !qa.ai && <p className="mt-2 text-xs text-muted">Проверено кодом: длина, шаблоны, призыв, цифры, близость к постам рынка.</p>}
            {canManage && version && version.number > 0 && (
              <button className="btn-ghost mt-3" disabled={running || !!busy} onClick={() => generate("qa")}>
                <ShieldCheck className="size-4" />Проверить ещё раз
              </button>
            )}
          </div>

          <div className="card text-sm">
            <h2 className="font-semibold">На чём написано</h2>
            {!ctx.search && !ctx.opportunity && !(ctx.market_posts ?? []).length ? (
              <p className="mt-1 text-muted">Контекст появится с первым черновиком.</p>
            ) : (
              <>
                {ctx.topic && <p className="mt-1 text-muted">Тема рынка: <b className="text-ink">{ctx.topic}</b></p>}
                {ctx.opportunity && <p className="mt-2">{ctx.opportunity.почему}</p>}
                {(ctx.market_posts ?? []).length > 0 && (
                  <>
                    <p className="label mt-3">Лучшее у рынка{ctx.search === "semantic" ? " (поиск по смыслу)" : ""}</p>
                    <ul className="space-y-2 text-xs">
                      {ctx.market_posts!.map((p) => (
                        <li key={p.post_id}>
                          <p className="line-clamp-2">{p.text}</p>
                          <p className="text-muted">{p.source}{p.overperformance ? ` · ×${p.overperformance.toFixed(1)} к медиане` : ""}
                            {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="ml-1 text-accent">открыть</a>}</p>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {ctx.audit && ctx.audit.слабые_места.length > 0 && (
                  <p className="mt-3 text-xs text-muted">Учтены слабые места аудита: {ctx.audit.слабые_места.map((w) => w.критерий).join(", ")}</p>
                )}
                <p className="mt-3 text-xs text-muted">Публикации конкурентов — только для анализа; QA проверяет, что текст на них не похож.</p>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
