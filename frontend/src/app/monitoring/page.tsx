"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  MonitoringDashboard,
  MonitoringIntegrationRow,
  MonitoringRuleRow,
  getMonitoringDashboard,
  listMonitoringIntegrations,
  listMonitoringRules,
} from "@/lib/api";

const statusBadge = (status: string | null | undefined): string => {
  const map: Record<string, string> = {
    ACTIVE: "bg-green-100 text-green-800",
    CONNECTING: "bg-blue-100 text-blue-800",
    PAUSED: "bg-yellow-100 text-yellow-800",
    DEGRADED: "bg-orange-100 text-orange-800",
    DISABLED: "bg-gray-100 text-gray-600",
    DISCONNECTED: "bg-gray-100 text-gray-600",
    DRAFT: "bg-blue-100 text-blue-800",
    FAILED: "bg-red-100 text-red-800",
    PASS: "bg-green-100 text-green-800",
    FAIL: "bg-red-100 text-red-800",
    INCONCLUSIVE: "bg-yellow-100 text-yellow-800",
  };
  return map[status || ""] || "bg-gray-100 text-gray-600";
};

const scheduleLabel = (schedule: Record<string, unknown>): string => {
  if (schedule.recurrence === "interval" && typeof schedule.minutes === "number") {
    return `Every ${schedule.minutes} min`;
  }
  if (schedule.recurrence === "cron" && typeof schedule.expression === "string") {
    return `Cron ${schedule.expression}`;
  }
  return String(schedule.recurrence ?? "—");
};

const fmt = (value: string | null | undefined): string => {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
};

export default function MonitoringPage() {
  const { token } = useAuth();

  const [dashboard, setDashboard] = useState<MonitoringDashboard | null>(null);
  const [rules, setRules] = useState<MonitoringRuleRow[]>([]);
  const [integrations, setIntegrations] = useState<MonitoringIntegrationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!token) return;
    setError(null);
    Promise.all([
      getMonitoringDashboard(token),
      listMonitoringRules(token),
      listMonitoringIntegrations(token),
    ])
      .then(([dash, ruleRows, integrationRows]) => {
        setDashboard(dash);
        setRules(ruleRows);
        setIntegrations(integrationRows);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load monitoring"))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (!token) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <p className="text-sm text-gray-500">Sign in to view monitoring.</p>
      </div>
    );
  }

  const statusKeys = ["ACTIVE", "PAUSED", "DISABLED", "DRAFT"] as const;

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Monitoring</h1>
        <p className="text-sm text-gray-500">
          Obligation monitoring rules and source-integration health (spec 3.15).
        </p>
      </div>

      <div className="flex justify-end">
        <Link
          href="/monitoring/new"
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700"
        >
          + New rule
        </Link>
      </div>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium text-gray-500 uppercase">Rules</p>
          <p className="mt-1 text-2xl font-semibold text-gray-900">
            {dashboard?.rules_total ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium text-gray-500 uppercase">Active</p>
          <p className="mt-1 text-2xl font-semibold text-green-700">
            {dashboard?.monitoring_active ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium text-gray-500 uppercase">Open exceptions</p>
          <p className="mt-1 text-2xl font-semibold text-red-700">
            {dashboard?.exceptions_open ? "Yes" : "No"}
          </p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium text-gray-500 uppercase">Last evaluation</p>
          <p className="mt-1 text-lg font-semibold text-gray-900">
            {dashboard?.last_evaluation_result ?? "—"}
          </p>
          <p className="text-xs text-gray-400">{fmt(dashboard?.last_evaluation_at)}</p>
        </div>
      </div>

      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-4 py-3 flex items-center justify-between">
          <h2 className="font-medium text-gray-800">Rules ({rules.length})</h2>
          <div className="flex flex-wrap gap-2 text-xs">
            {statusKeys.map((key) => (
              <span key={key} className="text-gray-500">
                {key}: {dashboard?.rules_by_status[key] ?? 0}
              </span>
            ))}
          </div>
        </div>
        {!loading && rules.length === 0 ? (
          <p className="p-4 text-sm text-gray-400">No monitoring rules configured yet.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {rules.map((rule) => (
              <li key={rule.id} className="flex items-center justify-between px-4 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-800">
                    {scheduleLabel(rule.schedule_definition)}
                    <span
                      className={`ml-2 inline-block px-2 py-0.5 rounded text-xs font-medium ${statusBadge(
                        rule.status
                      )}`}
                    >
                      {rule.status}
                    </span>
                    {rule.pause_reason && (
                      <span className="ml-2 text-xs text-gray-400">
                        ({rule.pause_reason})
                      </span>
                    )}
                  </p>
                  <p className="mt-1 text-xs text-gray-500">
                    Eval: {String(rule.evaluation_definition.kind ?? "?")}
                    {" · "}next run {fmt(rule.next_run_at)}
                  </p>
                </div>
                <div className="flex flex-col items-end gap-1 text-xs text-gray-500">
                  {rule.last_result && (
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${statusBadge(
                        rule.last_result
                      )}`}
                    >
                      {rule.last_result}
                    </span>
                  )}
                  <span>{fmt(rule.last_run_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="font-medium text-gray-800">
            Integrations ({integrations.length})
          </h2>
        </div>
        {!loading && integrations.length === 0 ? (
          <p className="p-4 text-sm text-gray-400">No integrations configured yet.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {integrations.map((integration) => {
              const health = integration.health;
              return (
                <li key={integration.id} className="flex items-center justify-between px-4 py-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800">
                      {integration.name}{" "}
                      <span className="text-xs text-gray-400">({integration.provider_key})</span>
                      <span
                        className={`ml-2 inline-block px-2 py-0.5 rounded text-xs font-medium ${statusBadge(
                          integration.status
                        )}`}
                      >
                        {integration.status}
                      </span>
                    </p>
                    <p className="mt-1 text-xs text-gray-500">
                      {health?.last_success_at
                        ? `last success ${fmt(health.last_success_at)}`
                        : "never connected successfully"}
                      {health && health.consecutive_failures > 0
                        ? ` · ${health.consecutive_failures} consecutive failure(s)`
                        : ""}
                    </p>
                    {health?.last_error && (
                      <p className="mt-1 text-xs text-red-600">{health.last_error}</p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}