"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { login, adminLogin, ssoStart } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [ssoLoading, setSSOLoading] = useState(false);
  const router = useRouter();
  const { login: authLogin } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      // System Admins use the dedicated hardened endpoint (Panels.txt):
      // unconditional MFA, stricter rate limits. Because MFA is mandatory
      // there, an admin without a code gets the MFA challenge from
      // /auth/admin/login itself — handled by the same catch below.
      const doLogin = () =>
        mfaCode
          ? adminLogin({ email, password, mfa_code: mfaCode })
          : login({ email, password, mfa_code: mfaCode || undefined });

      let result;
      try {
        result = await doLogin();
      } catch (err) {
        const msg = err instanceof Error ? err.message : "";
        if (/mfa/i.test(msg)) {
          // Server demanded an MFA code — reveal the field and retry.
          setMfaRequired(true);
          setError("Enter the 6-digit code from your authenticator app.");
          return;
        }
        throw err;
      }

      const user = await authLogin(result.access_token);
      if (user.is_admin) {
        router.push("/admin");
      } else {
        router.push("/dashboard");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const handleSSO = async () => {
    if (!email) {
      setError("Enter your work email to sign in with SSO.");
      return;
    }
    setError("");
    setSSOLoading(true);
    try {
      const result = await ssoStart(email);
      // Redirect the browser to the IdP; the authorization-code round-trip
      // comes back through /sso/callback with tokens in the fragment.
      window.location.href = result.authorization_url;
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not start SSO sign-in"
      );
      setSSOLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full space-y-8 p-8">
        <div>
          <h1 className="text-3xl font-bold text-center text-gray-900">
            ContractOS
          </h1>
          <h2 className="mt-2 text-center text-sm text-gray-600">
            Sign in to your account
          </h2>
        </div>

        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
              {error}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-gray-700"
              >
                Email address
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-gray-700"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            {mfaRequired && (
              <div>
                <label
                  htmlFor="mfa_code"
                  className="block text-sm font-medium text-gray-700"
                >
                  Authenticator code
                </label>
                <input
                  id="mfa_code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="000000"
                  className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 tracking-widest text-center text-lg"
                />
              </div>
            )}
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-gray-300" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="px-2 bg-white text-gray-500">or</span>
            </div>
          </div>

          <button
            type="button"
            onClick={handleSSO}
            disabled={ssoLoading}
            className="w-full flex justify-center py-2 px-4 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50"
          >
            {ssoLoading ? "Connecting..." : "Sign in with corporate SSO"}
          </button>

          <p className="text-center text-sm text-gray-600">
            Don&apos;t have an account?{" "}
            <Link
              href="/register"
              className="font-medium text-blue-600 hover:text-blue-500"
            >
              Register
            </Link>
          </p>

          <p className="text-center text-xs text-gray-400">
            <Link href="/legal/dmca" className="hover:text-gray-500">
              Copyright / DMCA Policy
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
