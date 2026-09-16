"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { getMetricsSummary, getMetricsAlerts, getQueueStats } from "@/lib/api";

interface Summary {
  uptime: number;
  requests: { total: number; errors: number };
  translations: { queue_size: number; completed_last_hour: number };
  database: Record<string, unknown>;
}

interface Alert {
  severity: string;
  message: string;
  metric: string;
  value: number;
  threshold: number;
}

interface QueueStats {
  total_jobs: number;
  by_status: Record<string, number>;
  oldest_pending: string | null;
}

const severityStyle: Record<string, string> = {
  critical: "bg-red-100 text-red-800 border-red-300",
  warning: "bg-yellow-100 text-yellow-800 border-yellow-300",
  info: "bg-blue-100 text-blue-800 border-blue-300",
};

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function ObservabilityPage() {
  const { token } = useAuth();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [queue, setQueue] = useState<QueueStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [s, a, q] = await Promise.all([
        getMetricsSummary(token).catch(() => null),
        getMetricsAlerts(token).catch(() => null),
        getQueueStats(token).catch(() => null),
      ]);
      if (s) setSummary(s);
      if (a) setAlerts(a.alerts);
      if (q) setQueue(q);
      setLastRefresh(new Date());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load metrics");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    queueMicrotask(() => load());
    timerRef.current = setInterval(load, 30000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [load]);

  const errorRate =
    summary && summary.requests.total > 0
      ? ((summary.requests.errors / summary.requests.total) * 100).toFixed(2)
      : "0.00";

  return (
    <div className="max-w-7xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Observability</h1>
          <p className="text-sm text-gray-500 mt-1">
            Live system metrics — auto-refreshes every 30s
            {lastRefresh && (
              <> · last updated {lastRefresh.toLocaleTimeString()}</>
            )}
          </p>
        </div>
        <button
          onClick={load}
          className="text-sm px-3 py-1.5 border border-gray-300 rounded-md hover:bg-gray-50"
        >
          Refresh now
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading metrics...</div>
      ) : (
        <div className="space-y-6">
          {/* Alerts */}
          {alerts.length > 0 && (
            <div className="space-y-2">
              {alerts.map((alert, i) => (
                <div
                  key={`${alert.metric}-${i}`}
                  className={`p-3 rounded-lg border text-sm ${
                    severityStyle[alert.severity] ?? severityStyle.info
                  }`}
                >
                  <span className="font-medium uppercase text-xs mr-2">
                    {alert.severity}
                  </span>
                  {alert.message}
                  <span className="text-xs opacity-70 ml-2">
                    ({alert.metric} = {alert.value}, threshold {alert.threshold})
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Key metrics */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-white shadow rounded-lg p-5">
              <div className="text-xs text-gray-500 uppercase tracking-wide">
                Uptime
              </div>
              <div className="mt-1 text-2xl font-semibold text-gray-900">
                {summary ? formatUptime(summary.uptime) : "—"}
              </div>
            </div>
            <div className="bg-white shadow rounded-lg p-5">
              <div className="text-xs text-gray-500 uppercase tracking-wide">
                API Requests
              </div>
              <div className="mt-1 text-2xl font-semibold text-gray-900">
                {summary ? summary.requests.total.toLocaleString() : "—"}
              </div>
              <div className="text-xs text-gray-400">error rate {errorRate}%</div>
            </div>
            <div className="bg-white shadow rounded-lg p-5">
              <div className="text-xs text-gray-500 uppercase tracking-wide">
                Translation Queue
              </div>
              <div className="mt-1 text-2xl font-semibold text-gray-900">
                {summary ? summary.translations.queue_size : "—"}
              </div>
              <div className="text-xs text-gray-400">
                {summary ? summary.translations.completed_last_hour : 0} completed last hour
              </div>
            </div>
            <div className="bg-white shadow rounded-lg p-5">
              <div className="text-xs text-gray-500 uppercase tracking-wide">
                Background Jobs
              </div>
              <div className="mt-1 text-2xl font-semibold text-gray-900">
                {queue ? queue.total_jobs : "—"}
              </div>
              <div className="text-xs text-gray-400">
                {queue
                  ? Object.entries(queue.by_status)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(" · ") || "no jobs"
                  : ""}
              </div>
            </div>
          </div>

          {/* Oldest pending */}
          {queue?.oldest_pending && (
            <div className="bg-white shadow rounded-lg p-5">
              <div className="text-xs text-gray-500 uppercase tracking-wide mb-1">
                Oldest Pending Job
              </div>
              <div className="text-sm text-gray-700">
                enqueued {new Date(queue.oldest_pending).toLocaleString()}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
