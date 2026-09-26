import type { GapInsight } from "@/lib/api";
import { fmtDate } from "@/lib/format";

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="label">{title}</p>
      <ul className="list-disc space-y-1 pl-5 text-sm">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}

/** AI-объяснение Content Gap — в списке пробелов и на странице темы. */
export function GapInsightView({ insight }: { insight: GapInsight }) {
  const d = insight.data;
  return (
    <div className="space-y-3">
      <p className="text-sm">{d.why}</p>
      <div className="grid gap-3 md:grid-cols-2">
        <List title="Подтверждают данные" items={d.evidence} />
        <List title="Кто раскрывает тему" items={d.competitors} />
        <List title="Что работает в теме" items={d.formats} />
        {d.risks && d.risks !== "нет" && (
          <div><p className="label">Учесть</p><p className="text-sm">{d.risks}</p></div>
        )}
      </div>
      {d.how_to_cover.length > 0 && (
        <div>
          <p className="label text-good">Как раскрыть вам</p>
          <ul className="space-y-2 text-sm">
            {d.how_to_cover.map((x, i) => (
              <li key={i} className="rounded-xl bg-bg p-2.5">
                {x.headline && <p className="font-medium">«{x.headline}»</p>}
                <p className="text-muted">{[x.angle, x.format].filter(Boolean).join(" · ")}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-muted">Объяснение за {insight.days} дней от {fmtDate(insight.created_at)}</p>
    </div>
  );
}
