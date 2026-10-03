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
  getMyMemberships,
  logout as apiLogout,
  clearSessionTokens,
  currentSessionTokens,
  onTokensRotated,
  persistSessionTokens,
  setSessionExpiredHandler,
  getCurrentOrganizationId,
  setCurrentOrganizationId,
  type MembershipOption,
} from "@/lib/api";
import { useRouter } from "next/navigation";

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
  /**
   * Organizations this user may act in. Empty while loading, and stays empty
   * for single-organization accounts, where the backend resolves the tenant
   * without a header.
   */
  organizations: MembershipOption[];
  /** Currently selected organization id, or null when only one applies. */
  currentOrganizationId: string | null;
  /** Make `organizationId` the tenant for subsequent requests, then reload. */
  switchOrganization: (organizationId: string) => Promise<void>;
  /** True once membership discovery has finished for this session. */
  organizationsLoaded: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [organizations, setOrganizations] = useState<MembershipOption[]>([]);
  const [organizationsLoaded, setOrganizationsLoaded] = useState(false);
  const [currentOrganizationId, setCurrentOrgId] = useState<string | null>(null);

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
      setOrganizations([]);
      setOrganizationsLoaded(false);
    });
    return () => {
      setSessionExpiredHandler(null);
    };
  }, []);

  // Discover which organizations this account may act in. Failure is not
  // fatal: single-organization accounts need no header, and a network blip
  // here must not log anyone out.
  useEffect(() => {
    let active = true;
    const session = currentSessionTokens();
    if (!session?.accessToken) {
      return;
    }
    getMyMemberships(session.accessToken)
      .then((memberships) => {
        if (!active) return;
        setOrganizations(memberships);

        // Reconcile the persisted selection: drop it if this session cannot
        // use it (membership suspended, different account signed in), so a
        // stale id cannot pin requests to an unreachable tenant.
        const stored = getCurrentOrganizationId();
        const valid = memberships.some((m) => m.organization_id === stored);
        const next =
          valid && stored ? stored : memberships.length === 1 ? memberships[0].organization_id : null;
        setCurrentOrganizationId(next);
        setCurrentOrgId(next);
      })
      .catch(() => {
        if (active) setOrganizations([]);
      })
      .finally(() => {
        if (active) setOrganizationsLoaded(true);
      });
    return () => {
      active = false;
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
    setOrganizations([]);
    setOrganizationsLoaded(false);
    setCurrentOrganizationId(null);
    setCurrentOrgId(null);
  };

  const switchOrganization = async (organizationId: string) => {
    if (!organizations.some((m) => m.organization_id === organizationId)) {
      throw new Error("Not a member of this organization");
    }
    setCurrentOrganizationId(organizationId);
    setCurrentOrgId(organizationId);
    // Every tenant-scoped query in the app now points at a different
    // organization, and client components hold the previous answers.
    // Refreshing re-runs them against the new tenant instead of leaving the
    // screen showing the old organization's data under the new name.
    router.refresh();
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        login,
        logout,
        isLoading,
        organizations,
        currentOrganizationId,
        switchOrganization,
        organizationsLoaded,
      }}
    >
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
