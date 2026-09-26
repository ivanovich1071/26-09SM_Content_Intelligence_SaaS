"use client";

import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";
import { MAIN_NAV, SETTINGS_NAV, type NavItem } from "@/lib/nav";

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={`flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition-all duration-200 ${
        active ? "bg-accent-soft font-semibold text-accent" : "text-ink hover:bg-bg"
      }`}
    >
      <Icon className="size-4" />
      {item.title}
    </Link>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { me, org, loading, logout, switchOrg } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
  }, [loading, me, router]);

  if (loading || !me) return <div className="p-8 text-sm text-muted">Загрузка…</div>;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-64 shrink-0 flex-col border-r border-line bg-surface p-4">
        <Link href="/dashboard" className="px-3 text-base font-extrabold">SM Content Intelligence</Link>
        {me.organizations.length > 1 ? (
          <select
            className="input mt-4"
            value={org?.id}
            onChange={(e) => {
              switchOrg(Number(e.target.value));
              router.refresh();
            }}
          >
            {me.organizations.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        ) : (
          <p className="mt-4 truncate px-3 text-sm text-muted">{org?.name}</p>
        )}
        <nav className="mt-4 space-y-0.5 overflow-y-auto">
          {MAIN_NAV.map((i) => <NavLink key={i.href} item={i} active={pathname.startsWith(i.href)} />)}
          <p className="px-3 pt-5 pb-1 text-xs font-semibold tracking-wide text-muted uppercase">Настройки</p>
          {SETTINGS_NAV.map((i) => <NavLink key={i.href} item={i} active={pathname.startsWith(i.href)} />)}
        </nav>
        <div className="mt-auto border-t border-line pt-3">
          <p className="truncate px-3 text-xs text-muted">{me.email}</p>
          <button
            className="mt-2 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm hover:bg-bg"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
          >
            <LogOut className="size-4" /> Выйти
          </button>
        </div>
      </aside>
      {/* key: при смене организации страница перемонтируется и перезапрашивает данные */}
      <main key={org?.id} className="min-w-0 flex-1 p-8">{children}</main>
    </div>
  );
}
