"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  ReactNode,
} from "react";
import {
  getMe,
  logout as apiLogout,
  clearSessionTokens,
  currentSessionTokens,
  onTokensRotated,
  persistSessionTokens,
  setSessionExpiredHandler,
} from "@/lib/api";

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
  login: (token: string) => Promise<User>;
  logout: () => void;
  isLoading: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Keep React state in sync with background refresh rotations. Without
  // this, the dashboard's `token` would go stale after the interceptor
  // refreshes, and requests would replay with the old token until reload.
  useEffect(() => {
    const unsubscribe = onTokensRotated((accessToken) => {
      setToken(accessToken);
    });
    return unsubscribe;
  }, []);

  // Fire the api-layer's session-expired handler into React state: when the
  // refresh token is dead, clear user/token so guards redirect to /login.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setToken(null);
      setUser(null);
    });
    return () => {
      setSessionExpiredHandler(null);
    };
  }, []);

  useEffect(() => {
    const session = currentSessionTokens();
    if (!session?.accessToken) {
      // No stored session — release the loading gate asynchronously so the
      // effect body never triggers a synchronous cascading render.
      queueMicrotask(() => setIsLoading(false));
      return;
    }

    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setToken(session.accessToken);
      getMe(session.accessToken)
        .then((me) => {
          if (active) setUser(me);
        })
        .catch(() => {
          clearSessionTokens();
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
    // Callers (login page) hand us the access token from the auth response;
    // the api layer already persisted the refresh token alongside it.
    persistSessionTokens({
      accessToken: newToken,
      refreshToken: currentSessionTokens()?.refreshToken ?? "",
    });
    setToken(newToken);
    // Hydrate the user BEFORE callers navigate away: guards on protected
    // pages (e.g. the dashboard layout) treat `user === null` as logged out
    // and would bounce the fresh session straight back to /login.
    const me = await getMe(newToken);
    setUser(me);
    return me;
  };

  const logout = () => {
    const current = token;
    if (current) {
      // Best-effort: close the backend session so online-time stops.
      apiLogout(current).catch(() => {});
    }
    clearSessionTokens();
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
