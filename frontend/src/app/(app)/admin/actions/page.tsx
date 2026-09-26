"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Pager } from "@/components/AdminUi";
import { PageHeader } from "@/components/ComingSoon";
import { ACTION_NAMES, describeAction, type AdminAction, type Paged } from "@/lib/admin";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";

export default function ActionsPage() {
  const [page, setPage] = useState(1);
  const [d, setD] = useState<Paged<AdminAction> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Paged<AdminAction>>(`/admin/actions?page=${page}`).then(setD).catch((e) => setError(e.message));
  }, [page]);

  return (
    <>
      <PageHeader title="Журнал" subtitle="Все изменения, сделанные через админку: кто, когда и что поменял." />
      {error && <p className="mb-3 text-sm text-bad">{error}</p>}
      <div className="card overflow-x-auto">
        {d === null ? <p className="text-sm text-muted">Загрузка…</p> : d.items.length === 0 ? <p className="text-sm text-muted">Действий ещё не было.</p> : (
          <table className="w-full text-sm [&_td]:pr-3 [&_th]:pr-3">
            <thead className="text-left text-xs text-muted">
              <tr><th className="py-1">Когда</th><th>Кто</th><th>Действие</th><th>Организация</th><th>Детали</th></tr>
            </thead>
            <tbody>
              {d.items.map((a) => (
                <tr key={a.id} className="border-t border-line align-top">
                  <td className="py-1.5 text-xs whitespace-nowrap">{fmtDate(a.at)}</td>
                  <td>{a.admin ?? "—"}</td>
                  <td>{ACTION_NAMES[a.action] ?? a.action} <span className="text-xs text-muted">{a.target_type} #{a.target_id}</span></td>
                  <td>{a.organization_id ? <Link href={`/admin/organizations/${a.organization_id}`} className="hover:text-accent">{a.organization}</Link> : "—"}</td>
                  <td className="max-w-md text-sm break-words">{describeAction(a)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {d && <Pager page={page} perPage={d.per_page} total={d.total} onPage={setPage} />}
      </div>
    </>
  );
}
