"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";

export default function SettingsPage() {
  const { user, token, logout } = useAuth();
  const [mfaSecret, setMfaSecret] = useState<string | null>(null);
  const [provisioningUri, setProvisioningUri] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [mfaEnabled, setMfaEnabled] = useState<boolean | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (token) {
      // The /auth/me response doesn't include mfa_enabled in the frontend
      // type, so we fetch it from the admin-free profile endpoint.
      fetch("/api/v1/auth/me", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then((data) => {
          if (typeof data.mfa_enabled === "boolean") setMfaEnabled(data.mfa_enabled);
        })
        .catch(() => {});
    }
  }, [token]);

  const handleSetupMfa = async () => {
    if (!token) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const res = await fetch("/api/v1/auth/mfa/setup", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "MFA setup failed");
      setMfaSecret(data.secret);
      setProvisioningUri(data.provisioning_uri);
    } catch (e) {
      setError(e instanceof Error ? e.message : "MFA setup failed");
    } finally {
      setBusy(false);
    }
  };

  const handleVerifyMfa = async () => {
    if (!token || !mfaCode) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const res = await fetch("/api/v1/auth/mfa/verify", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ code: mfaCode }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Verification failed");
      setMessage("MFA enabled successfully");
      setMfaEnabled(true);
      setMfaSecret(null);
      setProvisioningUri(null);
      setMfaCode("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Verification failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-sm text-gray-500 mt-1">
          Manage your profile and account security.
        </p>
      </div>

      {/* Profile */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Profile</h2>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <dt className="text-sm text-gray-500">Name</dt>
            <dd className="text-sm font-medium text-gray-900">{user?.name}</dd>
          </div>
          <div>
            <dt className="text-sm text-gray-500">Email</dt>
            <dd className="text-sm font-medium text-gray-900">{user?.email}</dd>
          </div>
          <div>
            <dt className="text-sm text-gray-500">Role</dt>
            <dd className="text-sm font-medium text-gray-900">
              {user?.is_admin ? "Administrator" : "User"}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-gray-500">Account</dt>
            <dd className="text-sm font-medium text-gray-900">Active</dd>
          </div>
        </dl>
      </div>

      {/* Security / MFA */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Security</h2>
          <span
            className={`text-xs px-2 py-0.5 rounded-full ${
              mfaEnabled ? "bg-emerald-100 text-emerald-800" : "bg-gray-100 text-gray-600"
            }`}
          >
            MFA {mfaEnabled ? "Enabled" : "Disabled"}
          </span>
        </div>

        {message && <div className="mb-4 text-sm text-emerald-700 bg-emerald-50 p-3 rounded-md">{message}</div>}
        {error && <div className="mb-4 text-sm text-rose-700 bg-rose-50 p-3 rounded-md">{error}</div>}

        {!mfaEnabled && !mfaSecret && (
          <button
            onClick={handleSetupMfa}
            disabled={busy}
            className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {busy ? "Setting up…" : "Enable Two-Factor Authentication"}
          </button>
        )}

        {mfaSecret && provisioningUri && (
          <div className="space-y-4">
            <p className="text-sm text-gray-600">
              Scan this QR code with your authenticator app (e.g. Google
              Authenticator, Authy), or enter the secret manually:
            </p>
            {/* QR is rendered by the authenticator app via the provisioning
                URI; we show the secret and a compact QR placeholder. */}
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
              <div className="text-xs text-gray-500 mb-1">Manual entry secret</div>
              <code className="text-sm text-gray-900 font-mono break-all">{mfaSecret}</code>
            </div>
            <div>
              <label className="block text-sm text-gray-600 mb-1">
                Enter the 6-digit code from your app
              </label>
              <div className="flex gap-3">
                <input
                  type="text"
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  placeholder="000000"
                  className="border border-gray-300 rounded-md px-3 py-2 text-sm w-40 tracking-widest"
                />
                <button
                  onClick={handleVerifyMfa}
                  disabled={busy || mfaCode.length < 6}
                  className="px-4 py-2 bg-emerald-600 text-white text-sm font-medium rounded-md hover:bg-emerald-700 disabled:opacity-50"
                >
                  {busy ? "Verifying…" : "Verify & Enable"}
                </button>
              </div>
            </div>
          </div>
        )}

        {mfaEnabled && (
          <p className="text-sm text-gray-600">
            Two-factor authentication is active on your account. Your
            password and a time-based code from your authenticator app are
            required to sign in.
          </p>
        )}
      </div>

      {/* Session */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-2">Session</h2>
        <p className="text-sm text-gray-600 mb-4">
          Sign out of this device. This closes your active sessions and
          revokes refresh tokens.
        </p>
        <button
          onClick={logout}
          className="px-4 py-2 bg-rose-600 text-white text-sm font-medium rounded-md hover:bg-rose-700"
        >
          Sign Out
        </button>
      </div>
    </div>
  );
}