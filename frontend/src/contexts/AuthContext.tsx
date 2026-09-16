"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  ReactNode,
} from "react";
import { getMe, logout as apiLogout } from "@/lib/api";

interface User {
  id: string;
  email: string;
  name: string;
  is_admin: boolean;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  /** Resolves once the session is fully hydrated (token stored + user fetched). */
  login: (token: string) => Promise<void>;
  logout: () => void;
  isLoading: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const savedToken = localStorage.getItem("token");
    if (!savedToken) {
      // No stored session — release the loading gate asynchronously so the
      // effect body never triggers a synchronous cascading render.
      queueMicrotask(() => setIsLoading(false));
      return;
    }

    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setToken(savedToken);
      getMe(savedToken)
        .then((me) => {
          if (active) setUser(me);
        })
        .catch(() => {
          localStorage.removeItem("token");
          if (active) setToken(null);
        })
        .finally(() => {
          if (active) setIsLoading(false);
        });
    });
    return () => {
      active = false;
    };
  }, []);

  const login = async (newToken: string) => {
    localStorage.setItem("token", newToken);
    setToken(newToken);
    // Hydrate the user BEFORE callers navigate away: guards on protected
    // pages (e.g. the dashboard layout) treat `user === null` as logged out
    // and would bounce the fresh session straight back to /login.
    setUser(await getMe(newToken));
  };

  const logout = () => {
    const current = token;
    if (current) {
      // Best-effort: close the backend session so online-time stops.
      apiLogout(current).catch(() => {});
    }
    localStorage.removeItem("token");
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, token, login, logout, isLoading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
