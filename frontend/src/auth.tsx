import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, jsonBody } from "./api";
import type { User } from "./types";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<User>;
  register: (username: string, displayName: string, password: string) => Promise<User>;
  updateProfile: (displayName: string) => Promise<User>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children, initialUser }: { children: ReactNode; initialUser?: User }) {
  const [user, setUser] = useState<User | null>(initialUser ?? null);
  const [loading, setLoading] = useState(!initialUser);

  useEffect(() => {
    if (initialUser) return;
    let active = true;
    api<User>("/api/auth/me")
      .then((value) => active && setUser(value))
      .catch(() => active && setUser(null))
      .finally(() => active && setLoading(false));
    const expired = () => setUser(null);
    window.addEventListener("auth:expired", expired);
    return () => {
      active = false;
      window.removeEventListener("auth:expired", expired);
    };
  }, [initialUser]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      login: async (username, password) => {
        const result = await api<User>("/api/auth/login", {
          method: "POST",
          ...jsonBody({ username, password }),
        });
        setUser(result);
        return result;
      },
      register: async (username, displayName, password) => {
        const result = await api<User>("/api/auth/register", {
          method: "POST",
          ...jsonBody({ username, display_name: displayName, password }),
        });
        setUser(result);
        return result;
      },
      updateProfile: async (displayName) => {
        const result = await api<User>("/api/auth/me", {
          method: "PATCH",
          ...jsonBody({ display_name: displayName }),
        });
        setUser(result);
        return result;
      },
      logout: async () => {
        await api<void>("/api/auth/logout", { method: "POST" });
        setUser(null);
      },
    }),
    [loading, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
