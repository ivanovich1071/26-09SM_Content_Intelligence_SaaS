"use client";

import { Loader2, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Taxonomy, type TaxonomySuggestion } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const UNIVERSAL_LABELS: Record<string, string> = {
  content_type: "Тип контента",
  funnel_stage: "Стадия воронки",
  hook_type: "Хук",
  cta_type: "Призыв (CTA)",
  proof_type: "Доказательство",
  tone: "Тон",
  value_type: "Ценность",
};

function Chips({ values, onRemove }: { values: string[]; onRemove?: (v: string) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {values.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 rounded-lg bg-accent-soft px-2.5 py-1 text-sm text-accent">
          {v}
          {onRemove && (
            <button type="button" onClick={() => onRemove(v)} title="Убрать" className="hover:text-bad">
              <X className="size-3.5" />
            </button>
          )}
        </span>
      ))}
    </div>
  );
}

function ListEditor({ label, hint, values, onChange, disabled }: {
  label: string; hint: string; values: string[]; onChange: (v: string[]) => void; disabled: boolean;
}) {
  const [draft, setDraft] = useState("");
  function add() {
    const items = draft.split(/[,;\n]/).map((s) => s.trim().toLowerCase()).filter(Boolean);
    onChange([...values, ...items.filter((i) => !values.includes(i))]);
    setDraft("");
  }
  return (
    <div>
      <p className="label">{label}</p>
      <p className="mb-2 text-xs text-muted">{hint}</p>
      <Chips values={values} onRemove={disabled ? undefined : (v) => onChange(values.filter((x) => x !== v))} />
      {!disabled && (
        <div className="mt-3 flex gap-2">
          <input className="input" value={draft} placeholder="Добавить (можно через запятую)"
                 onChange={(e) => setDraft(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
          <button type="button" className="btn-ghost" onClick={add}>Добавить</button>
        </div>
      )}
    </div>
  );
}

export default function CompanyPage() {
  const { org } = useAuth();
  const canEdit = org?.role === "owner" || org?.role === "admin";
  const [tax, setTax] = useState<Taxonomy | null>(null);
  const [topics, setTopics] = useState<string[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [niche, setNiche] = useState("");
  const [reclassify, setReclassify] = useState(true);
  const [suggestion, setSuggestion] = useState<TaxonomySuggestion | null>(null);
  const [busy, setBusy] = useState<"save" | "suggest" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function apply(t: Taxonomy) {
    setTax(t);
    setTopics(t.topics);
    setRoles(t.roles);
    setNiche(t.niche ?? "");
  }
  useEffect(() => {
    api<Taxonomy>("/taxonomy").then(apply).catch((e) => setError(e.message));
  }, []);

  async function suggest() {
    setError(null);
    setNotice(null);
    setBusy("suggest");
    try {
      const s = await api<TaxonomySuggestion>("/taxonomy/suggest", { method: "POST" });
      setSuggestion(s);
      setTopics(s.topics);
      setRoles(s.roles);
      setNiche(s.niche);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
    setBusy(null);
  }

  async function save() {
    setError(null);
    setNotice(null);
    setBusy("save");
    try {
      const t = await api<Taxonomy>("/taxonomy", { method: "PUT", json: { topics, roles, niche, reclassify } });
      apply(t);
      setSuggestion(null);
      setNotice(reclassify
        ? "Сохранено. Посты всех источников переразмечаются в фоне — прогресс виден в «Источниках»."
        : "Сохранено. Посты переразметятся при следующем сборе источников.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
    setBusy(null);
  }

  if (!tax) return error ? <p className="text-sm text-bad">{error}</p> : <p className="text-sm text-muted">Загрузка…</p>;
  const dirty = JSON.stringify([topics, roles, niche]) !== JSON.stringify([tax.topics, tax.roles, tax.niche ?? ""]);

  return (
    <>
      <PageHeader
        title="Компания"
        subtitle="Ниша, темы и роли аудитории — по ним модель размечает ваш контент и контент рынка, чтобы сравнивать доли тем и находить пробелы."
      />

      <div className="card space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold">Темы и аудитория</h2>
            <p className="text-sm text-muted">
              {tax.is_default
                ? "Сейчас общий набор для любой ниши. Настройте под себя — разметка станет точнее."
                : `Версия ${tax.version}${tax.updated_at ? ` от ${new Date(tax.updated_at).toLocaleDateString("ru-RU")}` : ""}.`}
            </p>
          </div>
          {canEdit && (
            <button className="btn-ghost" onClick={suggest} disabled={busy !== null}>
              {busy === "suggest" ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4 text-accent" />}
              Предложить по моим источникам
            </button>
          )}
        </div>

        {suggestion && (
          <div className="rounded-xl bg-accent-soft p-3 text-sm">
            <p className="font-medium">Предложение модели — проверьте и сохраните</p>
            <p className="mt-1 text-muted">{suggestion.rationale}</p>
            <p className="mt-1 text-xs text-muted">
              Опиралась на {suggestion.based_on.sources} источн. и {suggestion.based_on.posts} постов.
            </p>
          </div>
        )}

        <div>
          <label className="label" htmlFor="niche">Ниша</label>
          <input id="niche" className="input" value={niche} disabled={!canEdit} onChange={(e) => setNiche(e.target.value)}
                 placeholder="Например: кофейни Минска, гости 25–40 лет" />
        </div>
        <ListEditor label="Темы" hint="О чём пишут в вашей нише. «Другое» добавляется автоматически." values={topics}
                    onChange={setTopics} disabled={!canEdit} />
        <ListEditor label="Роли аудитории" hint="Кому адресован контент. «Широкая аудитория» добавляется автоматически."
                    values={roles} onChange={setRoles} disabled={!canEdit} />

        {canEdit && (
          <div className="flex flex-wrap items-center gap-4 border-t border-line pt-4">
            <button className="btn" onClick={save} disabled={busy !== null || !dirty}>
              {busy === "save" && <Loader2 className="size-4 animate-spin" />}Сохранить
            </button>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={reclassify} onChange={(e) => setReclassify(e.target.checked)} />
              Сразу переразметить посты (расходует AI-бюджет)
            </label>
          </div>
        )}
        {!canEdit && <p className="text-sm text-muted">Менять справочник могут владелец и администраторы.</p>}
        {notice && <p className="text-sm text-good">{notice}</p>}
        {error && <p className="text-sm text-bad">{error}</p>}
      </div>

      <div className="card mt-4">
        <h2 className="font-semibold">Универсальные поля</h2>
        <p className="mb-4 text-sm text-muted">Одинаковы для всех ниш — по ним сравниваются любые источники.</p>
        <div className="grid gap-4 md:grid-cols-2">
          {Object.entries(tax.universal).map(([field, values]) => (
            <div key={field}>
              <p className="label">{UNIVERSAL_LABELS[field] ?? field}</p>
              <p className="text-sm">{values.map((v) => v.replaceAll("_", " ")).join(" · ")}</p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
