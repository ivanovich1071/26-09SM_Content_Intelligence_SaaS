"use client";

import { Loader2, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Brand } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/format";

const TEXTS: { key: keyof Brand; label: string; hint?: string }[] = [
  { key: "company", label: "Компания" },
  { key: "website", label: "Сайт" },
  { key: "description", label: "Чем занимается компания" },
  { key: "offer", label: "Главное предложение клиенту" },
  { key: "audience", label: "Для кого" },
  { key: "cta", label: "Основной призыв к действию", hint: "Например: «Запишитесь на бесплатную диагностику»" },
  { key: "tone", label: "Тон", hint: "Как звучит компания: 1–2 предложения" },
];
const LISTS: { key: keyof Brand; label: string; hint: string }[] = [
  { key: "differentiators", label: "Отличия от конкурентов", hint: "По одному на строку" },
  { key: "proof_points", label: "Факты и доказательства",
    hint: "Кейсы, цифры, клиенты — только то, что можно упоминать. Других цифр в текстах не будет: вместо них — [шаблон]" },
  { key: "do", label: "Как пишем", hint: "Правила, по одному на строку" },
  { key: "dont", label: "Как не пишем", hint: "Запреты, по одному на строку" },
];

export default function BrandVoicePage() {
  const { org } = useAuth();
  const canManage = org?.role !== "viewer";
  const [b, setB] = useState<Brand | null>(null);
  const [example, setExample] = useState("");
  const [busy, setBusy] = useState<"save" | "suggest" | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api<Brand>("/brand").then((d) => { setB(d); setExample((d.examples ?? []).join("\n\n---\n\n")); })
      .catch((e) => setMsg({ ok: false, text: e.message }));
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!b) return;
    setBusy("save");
    setMsg(null);
    try {
      const d = await api<Brand>("/brand", { method: "PUT", json: {
        ...b, examples: example.split(/\n\s*---\s*\n/).map((x) => x.trim()).filter(Boolean) } });
      setB(d);
      setMsg({ ok: true, text: "Сохранено" });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Ошибка" });
    }
    setBusy(null);
  }

  async function suggest() {
    setBusy("suggest");
    setMsg(null);
    try {
      const d = await api<Brand>("/brand/suggest", { method: "POST", json: { website: b?.website || null } });
      setB((cur) => ({ ...(cur as Brand), ...Object.fromEntries(Object.entries(d).filter(([, v]) => Array.isArray(v) ? v.length : v)) }));
      if (d.examples?.length && !example) setExample(d.examples.join("\n\n---\n\n"));
      setMsg({ ok: true, text: "Черновик заполнен по сайту и вашим постам — проверьте и сохраните" });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Ошибка" });
    }
    setBusy(null);
  }

  if (!b) return <p className="text-sm text-muted">{msg?.text ?? "Загрузка…"}</p>;
  return (
    <>
      <PageHeader title="Brand Voice"
                  subtitle="Профиль компании и голос бренда — по ним Контент Завод пишет тексты. Факты берутся только отсюда: чего нет — будет [шаблоном]." />
      <form onSubmit={save} className="card grid gap-4 md:grid-cols-2">
        {TEXTS.map((f) => (
          <div key={f.key} className={f.key === "company" || f.key === "website" ? "" : "md:col-span-2"}>
            <label className="label" htmlFor={f.key}>{f.label}</label>
            {f.key === "company" || f.key === "website" ? (
              <input id={f.key} className="input" disabled={!canManage} value={(b[f.key] as string) ?? ""}
                     onChange={(e) => setB({ ...b, [f.key]: e.target.value })} />
            ) : (
              <textarea id={f.key} rows={2} className="input" disabled={!canManage} value={(b[f.key] as string) ?? ""}
                        onChange={(e) => setB({ ...b, [f.key]: e.target.value })} />
            )}
            {f.hint && <p className="mt-1 text-xs text-muted">{f.hint}</p>}
          </div>
        ))}
        {LISTS.map((f) => (
          <div key={f.key}>
            <label className="label" htmlFor={f.key}>{f.label}</label>
            <textarea id={f.key} rows={4} className="input" disabled={!canManage} value={(b[f.key] as string[]).join("\n")}
                      onChange={(e) => setB({ ...b, [f.key]: e.target.value.split("\n") })} />
            <p className="mt-1 text-xs text-muted">{f.hint}</p>
          </div>
        ))}
        <div className="md:col-span-2">
          <label className="label" htmlFor="examples">Образцы ваших текстов</label>
          <textarea id="examples" rows={5} className="input" disabled={!canManage} value={example}
                    onChange={(e) => setExample(e.target.value)} />
          <p className="mt-1 text-xs text-muted">Несколько удачных постов, разделённых строкой «---».</p>
        </div>
        {canManage && (
          <div className="flex flex-wrap items-center gap-3 md:col-span-2">
            <button className="btn" disabled={!!busy}>{busy === "save" && <Loader2 className="size-4 animate-spin" />}Сохранить</button>
            <button type="button" className="btn-ghost" disabled={!!busy} onClick={suggest}>
              {busy === "suggest" ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}Заполнить по сайту
            </button>
            {msg && <span className={`text-sm ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</span>}
            {b.updated_at && <span className="ml-auto text-xs text-muted">Обновлено {fmtDate(b.updated_at)}{b.source === "manual" ? "" : " · черновик модели"}</span>}
          </div>
        )}
      </form>
    </>
  );
}
