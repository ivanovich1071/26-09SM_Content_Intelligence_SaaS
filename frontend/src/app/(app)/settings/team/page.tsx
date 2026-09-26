"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/ComingSoon";
import { api, type Member, type Role } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ROLE_LABELS } from "@/lib/nav";

const ROLES: Role[] = ["owner", "admin", "member", "viewer"];

export default function TeamPage() {
  const { org, me } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const canManage = org?.role === "owner" || org?.role === "admin";

  const load = useCallback(() => {
    api<Member[]>("/organizations/current/members").then(setMembers).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>, ok?: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (ok) setNotice(ok);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  async function invite(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const email = String(f.get("email"));
    await act(() => api("/organizations/current/invitations", { method: "POST", json: { email, role: f.get("role") } }),
      `Приглашение для ${email} создано. Если пользователь ещё не зарегистрирован, он попадёт в команду при регистрации.`);
    form.reset();
  }

  return (
    <>
      <PageHeader title="Команда" subtitle={`Участники организации «${org?.name}».`} />
      <div className="card">
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr><th className="py-1">Участник</th><th>Роль</th><th /></tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id} className="border-t border-line">
                <td className="py-2">
                  <p className="font-medium">{m.full_name || m.email}</p>
                  {m.full_name && <p className="text-xs text-muted">{m.email}</p>}
                </td>
                <td>
                  {canManage && m.user_id !== me?.id ? (
                    <select
                      className="input w-44"
                      value={m.role}
                      onChange={(e) => act(() => api(`/organizations/current/members/${m.id}`,
                        { method: "PATCH", json: { role: e.target.value } }))}
                    >
                      {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                    </select>
                  ) : ROLE_LABELS[m.role]}
                </td>
                <td className="text-right">
                  {canManage && m.user_id !== me?.id && (
                    <button className="text-sm text-bad hover:underline"
                            onClick={() => act(() => api(`/organizations/current/members/${m.id}`, { method: "DELETE" }))}>
                      Удалить
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canManage && (
        <form onSubmit={invite} className="card mt-4 flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <label className="label" htmlFor="email">Пригласить по email</label>
            <input id="email" name="email" type="email" required className="input" />
          </div>
          <div>
            <label className="label" htmlFor="role">Роль</label>
            <select id="role" name="role" className="input w-44" defaultValue="member">
              {ROLES.filter((r) => r !== "owner" || org?.role === "owner").map((r) => (
                <option key={r} value={r}>{ROLE_LABELS[r]}</option>
              ))}
            </select>
          </div>
          <button className="btn">Пригласить</button>
        </form>
      )}
      {notice && <p className="mt-3 text-sm text-good">{notice}</p>}
      {error && <p className="mt-3 text-sm text-bad">{error}</p>}
    </>
  );
}
