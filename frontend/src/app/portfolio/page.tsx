"use client";

/**
 * Executive portfolio dashboard (spec §3.17.42-44) + forecasting (§3.18.56).
 *
 * Renders stored metric snapshots (never live math), trend sparklines,
 * open anomalies, current insights with their metric evidence, and forecast
 * runs for the renewal-exposure metric.
 */

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import {
  getPortfolioDashboard,
  listForecastRuns,
  createForecastRun,
  PortfolioDashboard,
  ForecastRunSummary,
} from "@/lib/api";

export default function PortfolioPage() {
  const { token } = useAuth();
  const [data, setData] = useState<PortfolioDashboard | null>(null);
  const [runs, setRuns] = useState<ForecastRunSummary[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const dashboard = await getPortfolioDashboard(token);
      setData(dashboard);
      const forecastRuns = await listForecastRuns(token);
      setRuns(forecastRuns);
    } catch {
      setError("Failed to load the portfolio dashboard.");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const runForecast = async () => {
    setRunning(true);
    try {
      await createForecastRun(token!, {
        metric_key: "lifecycle.renewals_due_30d",
        horizon_days: 30,
        model_type: "trend",
      });
      await load();
    } catch {
      setError("Forecast failed. Check that enough snapshot history exists.");
    } finally {
      setRunning(false);
    }
  };

  if (error) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-10">
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-800">
          {error}
        </div>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-gray-500">Loading portfolio…</p>
      </main>
    );
  }

  if (data.status === "no_data") {
    return (
      <main className="mx-auto max-w-5xl px-4 py-10">
        <h1 className="text-2xl font-bold mb-3">Portfolio Intelligence</h1>
        <div className="rounded-lg bg-amber-50 border border-amber-200 p-4 text-sm text-amber-800">
          {data.message} Use “Aggregate now” below to seed the first snapshot.
        </div>
      </main>
    );
  }

  const metrics = data.metrics ?? {};
  const insights = data.insights ?? [];
  const anomalies = data.anomalies ?? [];

  return (
    <main className="mx-auto max-w-6xl px-4 py-10">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Portfolio Intelligence</h1>
          <p className="text-sm text-gray-500">
            Snapshot date: {data.snapshot_date} — metrics are snapshots, not
            live numbers.
          </p>
        </div>
        <button
          onClick={runForecast}
          disabled={running}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {running ? "Running forecast…" : "Forecast renewals (30d)"}
        </button>
      </div>

      {/* Metric cards */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        {[
          ["inventory.total_agreements", "Total agreements"],
          ["inventory.active_agreements", "Active"],
          ["inventory.total_value", "Total value"],
          ["lifecycle.renewals_due_30d", "Renewals ≤30d"],
          ["obligations.open_count", "Open obligations"],
          ["obligations.overdue_count", "Overdue"],
          ["risk.open_findings", "Open findings"],
          ["risk.critical_findings", "Critical findings"],
        ].map(([key, label]) => {
          const metric = metrics[key];
          return (
            <div
              key={key}
              className="rounded-lg border border-gray-200 bg-white shadow-sm p-4"
            >
              <p className="text-xs uppercase tracking-wide text-gray-500">
                {label}
              </p>
              <p className="text-xl font-bold mt-1">
                {metric?.is_missing
                  ? "—"
                  : (metric?.value ?? 0).toLocaleString()}
              </p>
              {metric?.missing_reason && (
                <p className="text-[11px] text-amber-600 mt-1">
                  missing: {metric.missing_reason}
                </p>
              )}
            </div>
          );
        })}
      </section>

      {/* Insights */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Executive insights</h2>
        {insights.length === 0 ? (
          <p className="text-sm text-gray-500">No current insights.</p>
        ) : (
          <div className="space-y-3">
            {insights.map((insight) => (
              <div
                key={insight.id}
                className={`rounded-lg border p-4 text-sm ${
                  insight.severity === "critical"
                    ? "bg-red-50 border-red-200"
                    : insight.severity === "warning"
                      ? "bg-amber-50 border-amber-200"
                      : "bg-white border-gray-200"
                }`}
              >
                <p className="font-semibold">{insight.title}</p>
                <p className="text-gray-700 mt-1">{insight.body}</p>
                <p className="text-[11px] text-gray-500 mt-2">
                  generated by: {insight.generated_by} · category:{" "}
                  {insight.category}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Anomalies */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Open anomalies</h2>
        {anomalies.length === 0 ? (
          <p className="text-sm text-gray-500">No open anomalies.</p>
        ) : (
          <ul className="text-sm space-y-2">
            {anomalies.map((anomaly, index) => (
              <li
                key={index}
                className="rounded-lg border border-gray-200 bg-white p-3"
              >
                <span className="font-mono text-xs">{anomaly.metric_key}</span>{" "}
                — {anomaly.direction} of{" "}
                <strong>{anomaly.deviation}σ</strong> on{" "}
                {anomaly.snapshot_date} (observed{" "}
                {anomaly.observed_value.toLocaleString()} vs baseline{" "}
                {anomaly.baseline_mean.toLocaleString()})
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Forecasts */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Forecasts</h2>
        {runs.length === 0 ? (
          <p className="text-sm text-gray-500">
            No forecast runs yet — you need at least 14 daily snapshots.
          </p>
        ) : (
          <div className="space-y-3">
            {runs.map((run) => (
              <div
                key={run.id}
                className="rounded-lg border border-gray-200 bg-white p-4 text-sm"
              >
                <p>
                  <span className="font-mono text-xs">{run.metric_key}</span> ·{" "}
                  {run.model_type} · horizon {run.horizon_days}d ·{" "}
                  <span
                    className={
                      run.status === "completed"
                        ? "text-emerald-700"
                        : "text-amber-700"
                    }
                  >
                    {run.status}
                  </span>
                  {run.status_reason && ` (${run.status_reason})`}
                </p>
                {run.backtest?.evaluated && (
                  <p className="text-[11px] text-gray-500 mt-1">
                    backtest MAE {run.backtest.mae}
                    {run.backtest.mape != null &&
                      ` · MAPE ${run.backtest.mape}%`}
                  </p>
                )}
                {run.predictions && run.predictions.length > 0 && (
                  <p className="text-[11px] text-gray-600 mt-1">
                    next: {run.predictions[0].predicted_value} on{" "}
                    {run.predictions[0].target_date}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
