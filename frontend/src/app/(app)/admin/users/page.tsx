"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Pager } from "@/components/AdminUi";
import { PageHeader } from "@/components/ComingSoon";
import type { AdminUser, Paged } from "@/lib/admin";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/format";
import { ROLE_LABELS } from "@/lib/nav";

function Users() {
  const params = useSearchParams();
  const { me } = useAuth();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [flag, setFlag] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Paged<AdminUser> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    const qs = new URLSearchParams({ q, flag, page: String(page), per_page: "50" });
    api<Paged<AdminUser>>(`/admin/users?${qs}`).then(setData).catch((e) => setError(e.message));
  }, [q, flag, page]);
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  async function patch(u: AdminUser, change: Partial<Pick<AdminUser, "is_active" | "is_superadmin">>, confirmText: string) {
    if (!window.confirm(confirmText)) return;
    setError(null);
    try {
      await api(`/admin/users/${u.id}`, { method: "PATCH", json: change });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  return (
    <>
      <PageHeader title="Пользователи" subtitle="Блокировка сразу закрывает доступ: текущие сессии перестают работать, войти нельзя." />
      <div className="card mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-64 flex-1">
          <label className="label" htmlFor="q">Поиск</label>
          <input id="q" className="input" value={q} placeholder="email или имя" onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <div className="w-48">
          <label className="label" htmlFor="flag">Показать</label>
          <select id="flag" className="input" value={flag} onChange={(e) => { setFlag(e.target.value); setPage(1); }}>
            <option value="">всех</option><option value="blocked">заблокированных</option><option value="superadmin">суперадминов</option>
          </select>
        </div>
      </div>
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      <div className="card overflow-x-auto">
        {data === null ? <p className="text-sm text-muted">Загрузка…</p> : data.items.length === 0 ? <p className="text-sm text-muted">Никого не найдено.</p> : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr><th className="py-1">Пользователь</th><th>Организации</th><th>Был</th><th>Регистрация</th><th /></tr>
            </thead>
            <tbody>
              {data.items.map((u) => (
                <tr key={u.id} className="border-t border-line align-top">
                  <td className="py-2">
                    <p className={u.is_active ? "font-medium" : "font-medium text-muted line-through"}>{u.email}</p>
                    <p className="text-xs text-muted">{u.full_name}{u.is_superadmin && <span className="ml-1 font-semibold text-accent">суперадмин</span>}
                      {!u.is_active && <span className="ml-1 font-semibold text-bad">заблокирован</span>}</p>
                  </td>
                  <td className="text-xs">
                    {u.organizations.map((o) => (
                      <p key={o.id}><Link href={`/admin/organizations/${o.id}`} className="hover:text-accent">{o.name}</Link> <span className="text-muted">{ROLE_LABELS[o.role]}</span></p>
                    ))}
                  </td>
                  <td className="text-xs">{fmtDate(u.last_seen_at)}</td>
                  <td className="text-xs">{fmtDate(u.created_at)}</td>
                  <td className="text-right whitespace-nowrap">
                    {u.id !== me?.id && (
                      <>
                        <button className="btn-ghost text-xs" onClick={() => patch(u, { is_active: !u.is_active },
                          u.is_active ? `Заблокировать ${u.email}?` : `Разблокировать ${u.email}?`)}>
                          {u.is_active ? "Заблокировать" : "Разблокировать"}
                        </button>
                        <button className="btn-ghost text-xs" onClick={() => patch(u, { is_superadmin: !u.is_superadmin },
                          u.is_superadmin ? `Снять права суперадмина у ${u.email}?` : `Дать ${u.email} полный доступ к админке?`)}>
                          {u.is_superadmin ? "Снять админа" : "Сделать админом"}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && <Pager page={page} perPage={data.per_page} total={data.total} onPage={setPage} />}
      </div>
    </>
  );
}

export default function UsersPage() {
  return <Suspense><Users /></Suspense>;
}
