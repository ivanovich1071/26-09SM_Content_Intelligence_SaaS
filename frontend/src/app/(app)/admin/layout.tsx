"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ADMIN_NAV } from "@/lib/admin";
import { useAuth } from "@/lib/auth";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { me } = useAuth();
  const pathname = usePathname();
  if (!me?.is_superadmin) return <p className="text-sm text-muted">Раздел не найден.</p>;
  const active = (href: string) => (href === "/admin" ? pathname === href : pathname.startsWith(href));
  return (
    <>
      <nav className="no-print mb-6 flex flex-wrap gap-1 border-b border-line pb-2">
        {ADMIN_NAV.map((i) => (
          <Link key={i.href} href={i.href}
                className={`rounded-xl px-3 py-1.5 text-sm ${active(i.href) ? "bg-accent-soft font-semibold text-accent" : "hover:bg-bg"}`}>
            {i.title}
          </Link>
        ))}
      </nav>
      {children}
    </>
  );
}
