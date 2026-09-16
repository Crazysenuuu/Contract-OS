"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listFeatureFlags,
  createFeatureFlag,
  updateFeatureFlag,
  deleteFeatureFlag,
  evaluateFeatureFlag,
  getFeatureFlagStats,
  exportFeatureFlags,
} from "@/lib/api";

type Flag = Awaited<ReturnType<typeof listFeatureFlags>>[number];
type Stats = Awaited<ReturnType<typeof getFeatureFlagStats>>;

export default function FeatureFlagsAdminPage() {
  const { token, user } = useAuth();
  const isAdmin = !!user?.is_admin;
  const [flags, setFlags] = useState<Flag[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Create form
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [flagType, setFlagType] = useState("boolean");
  const [percentage, setPercentage] = useState(100);

  // Evaluate
  const [evalUser, setEvalUser] = useState("");
  const [evalResult, setEvalResult] = useState<Record<string, boolean> | null>(
    null
  );

  const reload = useCallback(() => {
    if (!token) return;
    setError(null);
    Promise.all([listFeatureFlags(token), getFeatureFlagStats(token)])
      .then(([f, s]) => {
        setFlags(f);
        setStats(s);
      })
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Failed to load flags")
      );
  }, [token]);

  useEffect(() => {
    if (isAdmin) void Promise.resolve().then(reload);
  }, [reload, isAdmin]);

  const handleCreate = async () => {
    if (!token || !name) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await createFeatureFlag(token, {
        name,
        description,
        flag_type: flagType,
        enabled: true,
        percentage: flagType === "percentage" ? percentage : undefined,
      });
      setNotice(`Flag "${name}" created`);
      setName("");
      setDescription("");
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create flag");
    } finally {
      setBusy(false);
    }
  };

  const handleToggle = async (flag: Flag) => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await updateFeatureFlag(token, flag.name, { enabled: !flag.enabled });
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update flag");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (flagName: string) => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await deleteFeatureFlag(token, flagName);
      setNotice(`Flag "${flagName}" deleted`);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete flag");
    } finally {
      setBusy(false);
    }
  };

  const handleEvaluate = async () => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const results: Record<string, boolean> = {};
      await Promise.all(
        flags.map(async (f) => {
          const r = await evaluateFeatureFlag(
            token,
            f.name,
            evalUser || undefined
          );
          results[f.name] = r.enabled;
        })
      );
      setEvalResult(results);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Evaluation failed");
    } finally {
      setBusy(false);
    }
  };

  const handleExport = async () => {
    if (!token) return;
    try {
      const data = await exportFeatureFlags(token);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "feature-flags.json";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    }
  };

  if (!isAdmin) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <p className="text-gray-500">Admin access required.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <main className="max-w-6xl mx-auto px-4 py-8">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <Link
              href="/admin"
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              ← Admin
            </Link>
            <h1 className="mt-2 text-2xl font-bold text-gray-900">
              Feature Flags
            </h1>
            <p className="mt-1 text-sm text-gray-600">
              Control feature rollout with boolean and percentage flags.
            </p>
          </div>
          <button
            onClick={handleExport}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            Export JSON
          </button>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-md bg-rose-50 border border-rose-200 text-sm text-rose-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-4 px-4 py-3 rounded-md bg-emerald-50 border border-emerald-200 text-sm text-emerald-700">
            {notice}
          </div>
        )}

        {/* Stats */}
        {stats && (
          <div className="mb-6 grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              ["Total", stats.total_flags],
              ["Enabled", stats.enabled_count],
              ["Active", stats.active_flags],
              ["Inactive", stats.inactive_flags],
            ].map(([label, value]) => (
              <div
                key={label as string}
                className="bg-white rounded-lg border border-gray-200 p-4"
              >
                <p className="text-xs text-gray-500">{label}</p>
                <p className="text-2xl font-semibold text-gray-900">
                  {value}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Create form */}
        <div className="mb-8 bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">
            Create flag
          </h2>
          <div className="grid gap-4 sm:grid-cols-4">
            <label className="sm:col-span-2">
              <span className="block text-xs font-medium text-gray-700 mb-1">
                Name
              </span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="new_clause_engine"
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
            <label className="sm:col-span-2">
              <span className="block text-xs font-medium text-gray-700 mb-1">
                Description
              </span>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
            <label>
              <span className="block text-xs font-medium text-gray-700 mb-1">
                Type
              </span>
              <select
                value={flagType}
                onChange={(e) => setFlagType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
              >
                <option value="boolean">boolean</option>
                <option value="percentage">percentage</option>
              </select>
            </label>
            {flagType === "percentage" && (
              <label>
                <span className="block text-xs font-medium text-gray-700 mb-1">
                  Rollout %
                </span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={percentage}
                  onChange={(e) => setPercentage(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                />
              </label>
            )}
          </div>
          <button
            onClick={handleCreate}
            disabled={busy || !name}
            className="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            Create
          </button>
        </div>

        {/* Evaluate */}
        <div className="mb-8 bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">
            Evaluate for user
          </h2>
          <div className="flex gap-2 items-center">
            <input
              value={evalUser}
              onChange={(e) => setEvalUser(e.target.value)}
              placeholder="User ID (optional — blank = anonymous)"
              className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm"
            />
            <button
              onClick={handleEvaluate}
              disabled={busy || flags.length === 0}
              className="px-4 py-2 text-sm font-medium text-white bg-gray-800 rounded-md hover:bg-gray-900 disabled:opacity-50"
            >
              Evaluate all
            </button>
          </div>
          {evalResult && (
            <div className="mt-3 flex flex-wrap gap-2">
              {Object.entries(evalResult).map(([flag, enabled]) => (
                <span
                  key={flag}
                  className={`px-2 py-1 text-xs rounded-full font-medium ${
                    enabled
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-gray-100 text-gray-500"
                  }`}
                >
                  {flag}: {enabled ? "on" : "off"}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Flag list */}
        <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
          {flags.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">No flags defined yet.</p>
          ) : (
            flags.map((f) => (
              <div
                key={f.name}
                className="p-4 flex items-center justify-between gap-4"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900">
                      {f.name}
                    </span>
                    <span className="px-2 py-0.5 text-[10px] rounded-full bg-gray-100 text-gray-600">
                      {f.flag_type}
                    </span>
                    {f.percentage < 100 && f.flag_type === "percentage" && (
                      <span className="px-2 py-0.5 text-[10px] rounded-full bg-amber-50 text-amber-700">
                        {f.percentage}%
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-gray-500 truncate">
                    {f.description}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => handleToggle(f)}
                    disabled={busy}
                    className={`px-3 py-1.5 text-xs font-medium rounded-md disabled:opacity-50 ${
                      f.enabled
                        ? "bg-emerald-100 text-emerald-800 hover:bg-emerald-200"
                        : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                    }`}
                  >
                    {f.enabled ? "Enabled" : "Disabled"}
                  </button>
                  <button
                    onClick={() => handleDelete(f.name)}
                    disabled={busy}
                    className="px-3 py-1.5 text-xs font-medium text-rose-600 bg-white border border-rose-200 rounded-md hover:bg-rose-50 disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </main>
    </div>
  );
}
