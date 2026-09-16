"use client";

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import { useAuth } from "./AuthContext";

interface FeatureFlagContextType {
  flags: Record<string, boolean>;
  isEnabled: (flagName: string) => boolean;
  isLoading: boolean;
  refresh: () => Promise<void>;
}

const FeatureFlagContext = createContext<FeatureFlagContextType>({
  flags: {},
  isEnabled: () => false,
  isLoading: true,
  refresh: async () => {},
});

export const useFeatureFlags = () => useContext(FeatureFlagContext);

export function FeatureFlagProvider({ children }: { children: ReactNode }) {
  const { user, token } = useAuth();
  const userId = user?.id;
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [isLoading, setIsLoading] = useState(true);

  const loadFlags = useCallback(async () => {
    if (!token) return;

    try {
      const response = await fetch("/api/v1/feature-flags/evaluate-all", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          user_id: userId,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setFlags(data);
      }
    } catch (err) {
      console.error("Failed to load feature flags:", err);
      // Default flags if API fails
      setFlags({
        ai_analysis: true,
        compliance_engine: true,
        translation_queue: true,
      });
    } finally {
      setIsLoading(false);
    }
  }, [token, userId]);

  useEffect(() => {
    if (!token) {
      // Logged out: reset flags and release the loading gate asynchronously
      // so the effect body never cascades synchronously.
      queueMicrotask(() => {
        setFlags({});
        setIsLoading(false);
      });
      return;
    }
    let active = true;
    queueMicrotask(() => {
      if (active) loadFlags();
    });
    return () => {
      active = false;
    };
  }, [token, loadFlags]);

  const isEnabled = (flagName: string): boolean => {
    return flags[flagName] ?? false;
  };

  const refresh = async () => {
    setIsLoading(true);
    await loadFlags();
  };

  return (
    <FeatureFlagContext.Provider value={{ flags, isEnabled, isLoading, refresh }}>
      {children}
    </FeatureFlagContext.Provider>
  );
}
