"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, session, type Me, type OrgBrief, type Tokens } from "./api";

type AuthState = {
  me: Me | null;
  org: OrgBrief | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (data: { email: string; password: string; full_name?: string; organization_name: string }) => Promise<void>;
  logout: () => void;
  switchOrg: (id: number) => void;
  reload: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [orgId, setOrgId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!session.hasToken()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      const data = await api<Me>("/auth/me");
      setMe(data);
      const saved = Number(session.orgId());
      const current = data.organizations.find((o) => o.id === saved) ?? data.organizations[0] ?? null;
      setOrgId(current?.id ?? null);
      session.setOrgId(current?.id ?? null);
    } catch {
      session.clear();
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const login = async (email: string, password: string) => {
    session.setTokens(await api<Tokens>("/auth/login", { method: "POST", json: { email, password } }));
    await reload();
  };

  const register: AuthState["register"] = async (data) => {
    session.clear();
    session.setTokens(await api<Tokens>("/auth/register", { method: "POST", json: data }));
    await reload();
  };

  const logout = () => {
    session.clear();
    setMe(null);
    setOrgId(null);
  };

  const switchOrg = (id: number) => {
    session.setOrgId(id);
    setOrgId(id);
  };

  const org = me?.organizations.find((o) => o.id === orgId) ?? null;
  return (
    <Ctx.Provider value={{ me, org, loading, login, register, logout, switchOrg, reload }}>{children}</Ctx.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth вне AuthProvider");
  return ctx;
}
