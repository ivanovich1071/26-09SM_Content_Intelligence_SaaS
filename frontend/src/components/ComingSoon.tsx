import { Construction } from "lucide-react";
import { MAIN_NAV, SETTINGS_NAV } from "@/lib/nav";

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="mb-6">
      <h1 className="text-2xl font-bold">{title}</h1>
      {subtitle && <p className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</p>}
    </header>
  );
}

export function ComingSoon({ href }: { href: string }) {
  const item = [...MAIN_NAV, ...SETTINGS_NAV].find((n) => n.href === href);
  if (!item) return null;
  return (
    <>
      <PageHeader title={item.title} subtitle={item.description} />
      <div className="card flex items-start gap-4">
        <Construction className="mt-0.5 size-5 shrink-0 text-warn" />
        <div className="text-sm">
          <p className="font-semibold">Раздел в разработке{item.epic ? ` — EPIC ${item.epic}` : ""}</p>
          <p className="mt-1 text-muted">План реализации — в ROADMAP.md репозитория.</p>
        </div>
      </div>
    </>
  );
}
