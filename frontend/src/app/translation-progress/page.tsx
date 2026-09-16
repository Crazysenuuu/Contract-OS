"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  getTranslationProgressDashboard,
} from "@/lib/api";

interface DashboardData {
  summary: {
    total: number;
    completed: number;
    pending: number;
    processing?: number;
    failed?: number;
    progress_percent: number;
    eta_seconds: number;
  };
  languages: Record<string, {
    total: number;
    completed: number;
    pending?: number;
    processing?: number;
    failed?: number;
    progress_percent: number;
    avg_processing_time: number;
  }>;
  workers: Array<{
    worker_id: string;
    hostname?: string | null;
    tasks_completed?: number;
    is_healthy: boolean;
  }>;
  throughput: {
    items_per_minute: number;
    items_last_hour: number;
    avg_processing_time_seconds?: number;
  };
  recent_completions: Array<{
    id: string;
    target_language: string;
    source_title?: string | null;
    completed_at: string | null;
    processing_time?: number | null;
    quality_score?: number | null;
  }>;
  live: {
    queue_depth: Array<{ time: string; count: number }>;
    active_workers: number;
    current_processing: Array<{ id: string; target_language: string; source_type?: string }>;
  };
  history: Array<{ hour: string; completed: number }>;
}

export default function TranslationProgressPage() {
  const router = useRouter();
  const { token } = useAuth();
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  // Declared before the effects that call it; no leading setLoading(true)
  // so the mount effect never cascades synchronously.
  const loadDashboard = useCallback(async () => {
    if (!token) return;
    try {
      const data = await getTranslationProgressDashboard(token);
      setDashboard(data);
    } catch (err) {
      console.error("Failed to load dashboard:", err);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!token) {
      router.push("/login");
      return;
    }
    queueMicrotask(() => loadDashboard());
  }, [token, loadDashboard, router]);

  useEffect(() => {
    if (autoRefresh && token) {
      intervalRef.current = setInterval(loadDashboard, 5000); // Refresh every 5 seconds
    }
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [autoRefresh, token, loadDashboard]);

  const formatTime = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${Math.round(seconds / 3600)}h`;
  };

  const getLanguageName = (code: string) => {
    const names: Record<string, string> = {
      en: "English",
      si: "Sinhala",
      ta: "Tamil",
      zh: "Chinese",
      ar: "Arabic",
      hi: "Hindi",
      ja: "Japanese",
      de: "German",
    };
    return names[code] || code;
  };

  const getProgressColor = (percent: number) => {
    if (percent >= 100) return "bg-green-500";
    if (percent >= 75) return "bg-blue-500";
    if (percent >= 50) return "bg-yellow-500";
    if (percent >= 25) return "bg-orange-500";
    return "bg-red-500";
  };

  return (
    <div className="max-w-7xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">📊 Translation Progress</h1>
          <p className="text-gray-500 mt-1">Real-time translation monitoring</p>
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded"
            />
            Auto-refresh (5s)
          </label>
          <button onClick={loadDashboard} className="text-blue-600 hover:underline">
            🔄 Refresh
          </button>
          <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
            ← Back
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading...</div>
      ) : dashboard ? (
        <>
          {/* Overall Progress */}
          <div className="bg-white rounded-lg border p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Overall Progress</h2>
              <div className="text-3xl font-bold text-blue-600">
                {dashboard.summary.progress_percent}%
              </div>
            </div>

            {/* Progress Bar */}
            <div className="w-full h-4 bg-gray-200 rounded-full mb-4">
              <div
                className={`h-full rounded-full transition-all duration-500 ${getProgressColor(dashboard.summary.progress_percent)}`}
                style={{ width: `${dashboard.summary.progress_percent}%` }}
              />
            </div>

            {/* Stats */}
            <div className="grid grid-cols-5 gap-4 text-center">
              <div>
                <div className="text-2xl font-bold">{dashboard.summary.total}</div>
                <div className="text-sm text-gray-500">Total</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-green-600">{dashboard.summary.completed}</div>
                <div className="text-sm text-gray-500">Completed</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-yellow-600">{dashboard.summary.pending}</div>
                <div className="text-sm text-gray-500">Pending</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-blue-600">{dashboard.summary.processing}</div>
                <div className="text-sm text-gray-500">Processing</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-red-600">{dashboard.summary.failed}</div>
                <div className="text-sm text-gray-500">Failed</div>
              </div>
            </div>

            {/* ETA */}
            <div className="mt-4 text-center text-gray-600">
              Estimated time remaining: <span className="font-semibold">{formatTime(dashboard.summary.eta_seconds)}</span>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-6 mb-6">
            {/* Throughput */}
            <div className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold mb-3">📈 Throughput</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-gray-600">Items/min:</span>
                  <span className="font-semibold">{dashboard.throughput.items_per_minute}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">Last hour:</span>
                  <span className="font-semibold">{dashboard.throughput.items_last_hour}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">Avg time:</span>
                  <span className="font-semibold">{dashboard.throughput.avg_processing_time_seconds ? formatTime(dashboard.throughput.avg_processing_time_seconds) : '—'}</span>
                </div>
              </div>
            </div>

            {/* Workers */}
            <div className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold mb-3">🖥️ Workers</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-gray-600">Active:</span>
                  <span className="font-semibold">{dashboard.live.active_workers}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">Processing:</span>
                  <span className="font-semibold">{dashboard.live.current_processing.length}</span>
                </div>
                {dashboard.workers.slice(0, 3).map((w) => (
                  <div key={w.worker_id} className="flex items-center gap-2 text-sm">
                    <span className={`w-2 h-2 rounded-full ${w.is_healthy ? "bg-green-500" : "bg-red-500"}`} />
                    <span className="truncate">{w.worker_id.slice(0, 12)}...</span>
                    <span className="text-gray-500 ml-auto">{w.tasks_completed} done</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Queue Depth Chart */}
            <div className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold mb-3">📉 Queue Depth (5min)</h3>
              <div className="flex items-end gap-1 h-24">
                {dashboard.live.queue_depth.map((point, i) => {
                  const maxCount = Math.max(...dashboard.live.queue_depth.map(p => p.count), 1);
                  const height = (point.count / maxCount) * 100;
                  return (
                    <div
                      key={i}
                      className="flex-1 bg-blue-500 rounded-t"
                      style={{ height: `${Math.max(height, 4)}%` }}
                      title={`${point.count} items`}
                    />
                  );
                })}
              </div>
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>5m ago</span>
                <span>Now</span>
              </div>
            </div>
          </div>

          {/* Language Progress */}
          <div className="bg-white rounded-lg border p-6 mb-6">
            <h2 className="text-lg font-semibold mb-4">🌐 Language Progress</h2>
            <div className="space-y-4">
              {Object.entries(dashboard.languages).map(([lang, data]) => (
                <div key={lang} className="flex items-center gap-4">
                  <div className="w-32">
                    <div className="font-medium">{getLanguageName(lang)}</div>
                    <div className="text-xs text-gray-500">{lang}</div>
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <div className="flex-1 h-3 bg-gray-200 rounded-full">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${getProgressColor(data.progress_percent)}`}
                          style={{ width: `${data.progress_percent}%` }}
                        />
                      </div>
                      <span className="text-sm font-semibold w-12 text-right">{data.progress_percent}%</span>
                    </div>
                    <div className="flex gap-4 text-xs text-gray-500">
                      <span>{data.completed}/{data.total} done</span>
                      <span>{data.pending} pending</span>
                      <span>{data.processing} processing</span>
                      {data.failed && data.failed > 0 && <span className="text-red-500">{data.failed} failed</span>}
                      <span>Avg: {formatTime(data.avg_processing_time)}</span>
                    </div>
                  </div>
                </div>
              ))}
              {Object.keys(dashboard.languages).length === 0 && (
                <div className="text-center text-gray-500 py-4">No translation tasks yet</div>
              )}
            </div>
          </div>

          {/* Current Processing */}
          {dashboard.live.current_processing.length > 0 && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
              <h3 className="font-semibold text-blue-900 mb-3">⚡ Currently Processing</h3>
              <div className="flex flex-wrap gap-2">
                {dashboard.live.current_processing.map((item) => (
                  <div
                    key={item.id}
                    className="bg-white rounded-lg px-3 py-2 border shadow-sm"
                  >
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                      <span className="font-medium">{getLanguageName(item.target_language)}</span>
                      <span className="text-xs text-gray-500">{item.source_type}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent Completions */}
          <div className="bg-white rounded-lg border p-6">
            <h2 className="text-lg font-semibold mb-4">✅ Recent Completions</h2>
            {dashboard.recent_completions.length === 0 ? (
              <div className="text-center text-gray-500 py-4">No completions yet</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2">Language</th>
                      <th className="text-left py-2">Title</th>
                      <th className="text-left py-2">Quality</th>
                      <th className="text-left py-2">Time</th>
                      <th className="text-left py-2">Completed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.recent_completions.map((item) => (
                      <tr key={item.id} className="border-b hover:bg-gray-50">
                        <td className="py-2">
                          <span className="font-medium">{getLanguageName(item.target_language)}</span>
                        </td>
                        <td className="py-2 text-gray-600 truncate max-w-[200px]">
                          {item.source_title || "—"}
                        </td>
                        <td className="py-2">
                          {item.quality_score ? (
                            <span className={`px-2 py-0.5 rounded text-xs ${
                              item.quality_score >= 0.8 ? "bg-green-100 text-green-700" :
                              item.quality_score >= 0.6 ? "bg-yellow-100 text-yellow-700" :
                              "bg-red-100 text-red-700"
                            }`}>
                              {Math.round(item.quality_score * 100)}%
                            </span>
                          ) : "—"}
                        </td>
                        <td className="py-2 text-gray-500">
                          {item.processing_time ? formatTime(item.processing_time) : "—"}
                        </td>
                        <td className="py-2 text-gray-500">
                          {item.completed_at ? new Date(item.completed_at).toLocaleTimeString() : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="bg-white rounded-lg border p-8 text-center">
          <div className="text-4xl mb-4">📊</div>
          <h2 className="text-xl font-semibold mb-2">Translation Progress Dashboard</h2>
          <p className="text-gray-500">
            Real-time monitoring of translation tasks across all languages.
          </p>
        </div>
      )}
    </div>
  );
}
