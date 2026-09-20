"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

/**
 * Client-side auth guard that waits for session hydration before deciding.
 *
 * Page effects run BEFORE the AuthProvider's hydration effect (React runs
 * child effects first), so a bare `if (!token) router.push("/login")` races
 * hydration and bounces legitimately-logged-in users to /login under load.
 * This hook defers the redirect until `isLoading` is false — i.e. the stored
 * session has been accepted or definitively rejected.
 */
export function useRequireAuth(): void {
  const { token, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !token) {
      router.push("/login");
    }
  }, [isLoading, token, router]);
}
