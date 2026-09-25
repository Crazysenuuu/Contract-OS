"use client";

import { useCallback, useEffect, useState } from "react";
import {
  MonitoringIntegrationRow,
  MonitoringRuleRow,
  listAgreementMonitoring,
  listMonitoringIntegrations,
} from "@/lib/api";

const statusBadge = (status: string | null | undefined): string => {
  const map: Record<string, string> = {
    ACTIVE: "bg-green-100 text-green-800",
    PAUSED: "bg-yellow-100 text-yellow-800",
    DISABLED: "bg-gray-100 text-gray-600",
    DRAFT: "bg-blue-100 text-blue-800",
    PASS: "bg-green-100 text-green-800",
    FAIL: "bg-red-100 text-red-800",
    DEGRADED: "bg-orange-100 text-orange-800",
    INCONCLUSIVE: "bg-yellow-100 text-yellow-800",
  };
  return map[status || ""] || "bg-gray-100 text-gray-600";
};

const fmt = (value: string | null | undefined): string => {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
};

export default function AgreementMonitoringPanel({
  agreementId,
  token,
}: {
  agreementId: string;
  token: string;
}) {
  const [rules, setRules] = useState<MonitoringRuleRow[]>([]);
  const [integrations, setIntegrations] = useState<MonitoringIntegrationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      listAgreementMonitoring(token, agreementId),
      listMonitoringIntegrations(token),
    ])
      .then(([ruleRows, integrationRows]) => {
        setRules(ruleRows);
        setIntegrations(integrationRows);
      })
      .catch(
        (err) =>
          setError(
            err instanceof Error ? err.message : "Failed to load monitoring"
          )
      )
      .finally(() => setLoading(false));
  }, [token, agreementId]);

  useEffect(() => {
    load();
  }, [load]);

  const sourceName = (integrationId: string): string =>
    integrations.find((i) => i.id === integrationId)?.name ?? "Unknown source";

  if (loading) {
    return (
      <div className="bg-white shadow rounded-lg p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Monitoring</h2>
        <p className="text-sm text-gray-400">Loading…</p>
      </div>
    );
  }

  return (
    <div className="bg-white shadow rounded-lg p-6">
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-lg font-medium text-gray-900">Monitoring</h2>
        {rules.length > 0 && (
          <span className="text-xs text-gray-400">
            {rules.length} rule{rules.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {error && (
        <div className="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}

      {rules.length === 0 ? (
        <p className="text-sm text-gray-500">
          No monitoring rules configured for this agreement yet.
        </p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {rules.map((rule) => {
            const inconclusive = rule.last_result === "INCONCLUSIVE";
            const degraded =
              (rule.last_result === "FAIL" && !inconclusive) || inconclusive;
            return (
              <li key={rule.id} className="py-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-gray-800">
                    {sourceName(rule.integration_id)}
                  </p>
                  <div className="flex items-center gap-2">
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${statusBadge(
                        rule.status
                      )}`}
                    >
                      {rule.status}
                    </span>
                    {rule.last_result && (
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${statusBadge(
                          rule.last_result
                        )}`}
                      >
                        {rule.last_result}
                      </span>
                    )}
                  </div>
                </div>

                <p className="mt-1 text-xs text-gray-500">
                  Eval: {String(rule.evaluation_definition.kind ?? "?")}
                  {" · "}last checked {fmt(rule.last_run_at)}
                </p>

                {degraded && (
                  <p className="mt-1 text-xs text-orange-700">
                    {inconclusive
                      ? "Source unavailable — result is inconclusive, not a pass."
                      : "Source unavailable or check failed — last result reflects a failure."}
                  </p>
                )}

                {integrationHealthLastError(rule.integration_id, integrations) && (
                  <p className="mt-1 text-xs text-red-600">
                    Last failure:{" "}
                    {integrationHealthLastError(rule.integration_id, integrations)}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function integrationHealthLastError(
  integrationId: string,
  integrations: MonitoringIntegrationRow[]
): string | null {
  const health = integrations.find((i) => i.id === integrationId)?.health;
  return health?.last_error ?? null;
}