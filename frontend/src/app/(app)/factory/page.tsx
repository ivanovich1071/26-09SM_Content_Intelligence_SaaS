"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ComingSoon } from "@/components/ComingSoon";
import { api, type Opportunities } from "@/lib/api";
import { FORMAT_NAMES } from "@/lib/audit";

export default function Page() {
  const [queue, setQueue] = useState<Opportunities["items"]>([]);
  useEffect(() => {
    api<Opportunities>("/strategy/opportunities?view=in_factory").then((d) => setQueue(d.items)).catch(() => null);
  }, []);
  return (
    <>
      <ComingSoon href="/factory" />
      <div className="card mt-4">
        <h2 className="font-semibold">Очередь тем</h2>
        {queue.length === 0 ? (
          <p className="mt-1 text-sm text-muted">
            Пусто. Отправьте темы из <Link href="/strategy" className="text-accent">«Стратегии»</Link> кнопкой «В Контент Завод».
          </p>
        ) : (
          <ul className="mt-2 divide-y divide-line text-sm">
            {queue.map((o) => (
              <li key={o.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>{o.title}</span>
                <span className="text-xs text-muted">{o.formats.map((f) => FORMAT_NAMES[f] ?? f).join(", ")}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
