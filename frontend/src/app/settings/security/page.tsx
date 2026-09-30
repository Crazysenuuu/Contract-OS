"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  changePassword,
  disableMfa,
  listMySessions,
  revokeMySession,
  listMyDevices,
  removeMyDevice,
  getMe,
  UserSessionInfo,
  UserDeviceInfo,
} from "@/lib/api";

function deviceLabel(ua: string | null): string {
  if (!ua) return "Unknown device";
  // Lightweight UA parsing — enough for a session list.
  const browser =
    /Edg\//.test(ua)
      ? "Edge"
      : /OPR\//.test(ua)
      ? "Opera"
      : /Chrome\//.test(ua)
      ? "Chrome"
      : /Safari\//.test(ua)
      ? "Safari"
      : /Firefox\//.test(ua)
      ? "Firefox"
      : "Browser";
  const os = /Windows/.test(ua)
    ? "Windows"
    : /Mac OS X/.test(ua)
    ? "macOS"
    : /Android/.test(ua)
    ? "Android"
    : /iPhone|iPad/.test(ua)
    ? "iOS"
    : /Linux/.test(ua)
    ? "Linux"
    : "";
  return os ? `${browser} on ${os}` : browser;
}

function formatWhen(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function SecuritySettingsPage() {
  const { token, logout } = useAuth();

  // ---- MFA state ----
  const [mfaEnabled, setMfaEnabled] = useState<boolean | null>(null);
  const [mfaSecret, setMfaSecret] = useState<string | null>(null);
  const [provisioningUri, setProvisioningUri] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [showDisable, setShowDisable] = useState(false);

  // ---- Password state ----
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  // ---- Sessions & devices ----
  const [sessions, setSessions] = useState<UserSessionInfo[]>([]);
  const [devices, setDevices] = useState<UserDeviceInfo[]>([]);

  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const reloadSessions = useCallback(() => {
    if (!token) return;
    listMySessions(token)
      .then(setSessions)
      .catch(() => {});
  }, [token]);

  const reloadDevices = useCallback(() => {
    if (!token) return;
    listMyDevices(token)
      .then(setDevices)
      .catch(() => setDevices([]));
  }, [token]);

  useEffect(() => {
    if (!token) return;
    getMe(token)
      .then((me) => setMfaEnabled(!!me.mfa_enabled))
      .catch(() => setMfaEnabled(false));
    reloadSessions();
    reloadDevices();
  }, [token, reloadSessions, reloadDevices]);

  const run = async (fn: () => Promise<void>, okMessage: string) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
      setMessage(okMessage);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operation failed");
    } finally {
      setBusy(false);
    }
  };

  const handleSetupMfa = () =>
    run(async () => {
      const res = await fetch("/api/v1/auth/mfa/setup", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "MFA setup failed");
      setMfaSecret(data.secret);
      setProvisioningUri(data.provisioning_uri);
    }, "Scan the secret with your authenticator app, then verify.");

  const handleVerifyMfa = () =>
    run(async () => {
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
      setMfaEnabled(true);
      setMfaSecret(null);
      setProvisioningUri(null);
      setMfaCode("");
    }, "MFA enabled successfully.");

  const handleDisableMfa = () =>
    run(async () => {
      await disableMfa(token!, {
        password: disablePassword,
        code: disableCode,
      });
      setMfaEnabled(false);
      setShowDisable(false);
      setDisablePassword("");
      setDisableCode("");
    }, "MFA disabled.");

  const handleChangePassword = () =>
    run(async () => {
      if (newPassword !== confirmPassword) {
        throw new Error("New passwords do not match");
      }
      const r = await changePassword(token!, {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setMessage(r.message);
    }, "Password updated. Other sessions were signed out.");

  const handleRevokeSession = (s: UserSessionInfo) =>
    run(async () => {
      await revokeMySession(token!, s.id);
      reloadSessions();
    }, "Session revoked.");

  const handleRemoveDevice = (d: UserDeviceInfo) =>
    run(async () => {
      await removeMyDevice(token!, d.device_id);
      reloadDevices();
    }, "Device removed.");

  const section = "bg-white rounded-xl shadow-sm border border-gray-100 p-6";
  const inputCls =
    "block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm";
  const labelCls = "block text-sm font-medium text-gray-700 mb-1";

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <div className="flex items-center gap-2 text-sm text-gray-500 mb-1">
          <Link href="/settings" className="hover:text-gray-700">
            ← Settings
          </Link>
        </div>
        <h1 className="text-2xl font-bold text-gray-900">Security</h1>
        <p className="text-sm text-gray-500 mt-1">
          Password, two-factor authentication, sessions, and devices.
        </p>
      </div>

      {message && (
        <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 p-3 rounded-md">
          {message}
        </div>
      )}
      {error && (
        <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 p-3 rounded-md">
          {error}
        </div>
      )}

      {/* Password */}
      <div className={section}>
        <h2 className="text-lg font-semibold text-gray-900 mb-4">
          Change Password
        </h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleChangePassword();
          }}
          className="space-y-3"
        >
          <div>
            <label className={labelCls}>Current password</label>
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              className={inputCls}
              autoComplete="current-password"
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>New password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
                className={inputCls}
                autoComplete="new-password"
              />
            </div>
            <div>
              <label className={labelCls}>Confirm new password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
                className={inputCls}
                autoComplete="new-password"
              />
            </div>
          </div>
          <p className="text-xs text-gray-500">
            Changing your password signs out all other devices.
          </p>
          <button
            type="submit"
            disabled={busy}
            className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {busy ? "Updating…" : "Update Password"}
          </button>
        </form>
      </div>

      {/* MFA */}
      <div className={section}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">
            Two-Factor Authentication
          </h2>
          {mfaEnabled !== null && (
            <span
              className={`text-xs px-2 py-0.5 rounded-full ${
                mfaEnabled
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-gray-100 text-gray-600"
              }`}
            >
              {mfaEnabled ? "Enabled" : "Disabled"}
            </span>
          )}
        </div>

        {mfaEnabled === null ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : !mfaEnabled && !mfaSecret ? (
          <button
            onClick={handleSetupMfa}
            disabled={busy}
            className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {busy ? "Setting up…" : "Enable Two-Factor Authentication"}
          </button>
        ) : !mfaEnabled && mfaSecret ? (
          <div className="space-y-4">
            <p className="text-sm text-gray-600">
              Scan this secret with your authenticator app (Google
              Authenticator, Authy, 1Password), or enter it manually:
            </p>
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
              <div className="text-xs text-gray-500 mb-1">
                Manual entry secret
              </div>
              <code className="text-sm text-gray-900 font-mono break-all">
                {mfaSecret}
              </code>
            </div>
            {provisioningUri && (
              <details className="text-xs text-gray-500">
                <summary className="cursor-pointer">Provisioning URI</summary>
                <code className="block mt-1 break-all">{provisioningUri}</code>
              </details>
            )}
            <div>
              <label className={labelCls}>
                Enter the 6-digit code from your app
              </label>
              <div className="flex gap-3">
                <input
                  type="text"
                  value={mfaCode}
                  onChange={(e) =>
                    setMfaCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                  }
                  placeholder="000000"
                  className="border border-gray-300 rounded-md px-3 py-2 text-sm w-40 tracking-widest text-center"
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
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-gray-600">
              Two-factor authentication is active. Your password and a
              time-based code from your authenticator app are required to
              sign in.
            </p>
            {!showDisable ? (
              <button
                onClick={() => setShowDisable(true)}
                className="px-4 py-2 bg-white border border-rose-300 text-rose-700 text-sm font-medium rounded-md hover:bg-rose-50"
              >
                Disable MFA…
              </button>
            ) : (
              <div className="border border-rose-200 bg-rose-50 rounded-lg p-4 space-y-3">
                <p className="text-sm text-rose-800">
                  Confirm your password <strong>and</strong> a current
                  authenticator code to disable MFA.
                </p>
                <input
                  type="password"
                  value={disablePassword}
                  onChange={(e) => setDisablePassword(e.target.value)}
                  placeholder="Password"
                  className={inputCls}
                  autoComplete="current-password"
                />
                <input
                  type="text"
                  value={disableCode}
                  onChange={(e) =>
                    setDisableCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                  }
                  placeholder="000000"
                  className="border border-gray-300 rounded-md px-3 py-2 text-sm w-40 tracking-widest text-center"
                />
                <div className="flex gap-2">
                  <button
                    onClick={handleDisableMfa}
                    disabled={busy || !disablePassword || disableCode.length < 6}
                    className="px-4 py-2 bg-rose-600 text-white text-sm font-medium rounded-md hover:bg-rose-700 disabled:opacity-50"
                  >
                    {busy ? "Disabling…" : "Disable MFA"}
                  </button>
                  <button
                    onClick={() => setShowDisable(false)}
                    className="px-4 py-2 bg-white border border-gray-300 text-gray-700 text-sm font-medium rounded-md hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Sessions */}
      <div className={section}>
        <h2 className="text-lg font-semibold text-gray-900 mb-4">
          Active Sessions
        </h2>
        {sessions.length === 0 ? (
          <p className="text-sm text-gray-500">No sessions recorded.</p>
        ) : (
          <div className="divide-y divide-gray-100">
            {sessions.map((s) => {
              const active = s.status === "active";
              return (
                <div
                  key={s.id}
                  className="py-3 flex items-start justify-between gap-4"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900">
                      {deviceLabel(s.user_agent)}
                      {!active && (
                        <span className="ml-2 text-[10px] uppercase text-gray-400">
                          {s.status.replace(/_/g, " ")}
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-gray-500">
                      IP {s.ip_address ?? "unknown"} · started{" "}
                      {formatWhen(s.login_at)}
                    </p>
                  </div>
                  {active && (
                    <button
                      onClick={() => handleRevokeSession(s)}
                      disabled={busy}
                      className="px-2 py-1 text-xs font-medium rounded-md border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-50 shrink-0"
                    >
                      Revoke
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
        <button
          onClick={logout}
          className="mt-4 px-4 py-2 bg-rose-600 text-white text-sm font-medium rounded-md hover:bg-rose-700"
        >
          Sign out everywhere
        </button>
      </div>

      {/* Devices */}
      <div className={section}>
        <h2 className="text-lg font-semibold text-gray-900 mb-2">
          Registered Devices
        </h2>
        <p className="text-sm text-gray-500 mb-4">
          Mobile devices registered for push notifications.
        </p>
        {devices.length === 0 ? (
          <p className="text-sm text-gray-500">
            No devices registered. Install the mobile app and sign in to
            register this device.
          </p>
        ) : (
          <div className="divide-y divide-gray-100">
            {devices.map((d) => {
              const revoked = d.revoked_at !== null;
              return (
                <div
                  key={d.id}
                  className="py-3 flex items-start justify-between gap-4"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 capitalize">
                      {d.platform}
                      {d.app_version ? ` · v${d.app_version}` : ""}
                      {revoked && (
                        <span className="ml-2 text-[10px] uppercase text-gray-400">
                          revoked
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-gray-500 truncate">
                      {d.device_id} · last seen {formatWhen(d.last_seen_at)}
                    </p>
                  </div>
                  {!revoked && (
                    <button
                      onClick={() => handleRemoveDevice(d)}
                      disabled={busy}
                      className="px-2 py-1 text-xs font-medium rounded-md border border-rose-200 text-rose-600 hover:bg-rose-50 disabled:opacity-50 shrink-0"
                    >
                      Remove
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
