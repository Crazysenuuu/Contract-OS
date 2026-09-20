"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { currentSessionTokens, persistSessionTokens } from "@/lib/api";

export default function SSOCallbackPage() {
  const [error, setError] = useState("");
  const router = useRouter();
  const { login: authLogin } = useAuth();

  useEffect(() => {
    const finish = async () => {
      // Tokens arrive in the URL fragment (never the query string) so they
      // are not logged by proxies. Strip the leading '#'.
      const hash = window.location.hash.replace(/^#/, "");
      const params = new URLSearchParams(hash);
      const accessToken = params.get("access_token");
      if (!accessToken) {
        setError("SSO sign-in did not return a session token.");
        return;
      }
      // Persist the refresh token from the fragment so the session can be
      // renewed after the 15-minute access token expires. authLogin below
      // preserves whatever refresh token is already in the store.
      persistSessionTokens({
        accessToken,
        refreshToken:
          params.get("refresh_token") ??
          currentSessionTokens()?.refreshToken ??
          "",
      });
      try {
        await authLogin(accessToken);
        router.replace("/dashboard");
      } catch (err) {
        setError(err instanceof Error ? err.message : "SSO sign-in failed");
      }
    };
    finish();
  }, [router, authLogin]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full space-y-8 p-8 text-center">
        <h1 className="text-2xl font-bold text-gray-900">Completing sign-in</h1>
        {error ? (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
            {error}
          </div>
        ) : (
          <p className="text-sm text-gray-600">Redirecting to your dashboard…</p>
        )}
      </div>
    </div>
  );
}