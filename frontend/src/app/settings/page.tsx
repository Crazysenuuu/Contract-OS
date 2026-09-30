"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { updateProfile } from "@/lib/api";

export default function SettingsPage() {
  const { user, token, logout } = useAuth();
  const [name, setName] = useState(user?.name ?? "");
  const [phone, setPhone] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loadedPhone, setLoadedPhone] = useState(false);

  // Load the phone value once /auth/me data is available (the AuthContext
  // user doesn't carry phone).
  useEffect(() => {
    if (!token || loadedPhone) return;
    setLoadedPhone(true);
    fetch("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.phone) setPhone(data.phone);
        if (data?.name) setName(data.name);
      })
      .catch(() => {});
  }, [token, loadedPhone]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const updated = await updateProfile(token, {
        name: name.trim() || undefined,
        phone: phone.trim() || null,
      });
      setMessage("Profile updated.");
      if (updated.name) setName(updated.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update profile");
    } finally {
      setSaving(false);
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

      {/* Profile */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Profile</h2>
        <form onSubmit={handleSave} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Phone (E.164, for SMS alerts)
              </label>
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+94771234567"
                className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
              />
            </div>
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
          <button
            type="submit"
            disabled={saving}
            className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save Profile"}
          </button>
        </form>
      </div>

      {/* Security */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-2">Security</h2>
        <p className="text-sm text-gray-600 mb-4">
          Password, two-factor authentication, active sessions, and registered
          devices.
        </p>
        <Link
          href="/settings/security"
          className="inline-block px-4 py-2 bg-gray-900 text-white text-sm font-medium rounded-md hover:bg-gray-800"
        >
          Open Security Settings →
        </Link>
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
