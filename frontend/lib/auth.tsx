"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError, UNAUTHORIZED_EVENT } from "./api";
import type { Me, Meta, Permission } from "./types";

interface AuthContextValue {
  me: Me | null;
  meta: Meta | null;
  loading: boolean;
  can: (permission: Permission) => boolean;
  reload: () => Promise<void>;
  logout: () => Promise<void>;
  switchOrganization: (organizationId: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      setMe(await api<Me>("/auth/me"));
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) console.error(error);
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    api<Meta>("/meta").then(setMeta).catch(() => setMeta(null));
    const onUnauthorized = () => setMe(null);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [reload]);

  const logout = useCallback(async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    setMe(null);
  }, []);

  const switchOrganization = useCallback(
    async (organizationId: string) => {
      await api("/auth/switch-organization", { method: "POST", body: { organization_id: organizationId } });
      await reload();
    },
    [reload],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      me,
      meta,
      loading,
      can: (permission: Permission) => Boolean(me?.permissions.includes(permission)),
      reload,
      logout,
      switchOrganization,
    }),
    [me, meta, loading, reload, logout, switchOrganization],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
