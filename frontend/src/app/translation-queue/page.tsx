"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  getTranslationQueueStats,
  listTranslationQueue,
  retryFailedTranslations,
  cancelTranslation,
  processTranslationQueue,
} from "@/lib/api";

interface QueueStats {
  total: number;
  by_status: Record<string, number>;
  by_priority: Record<string, number>;
  by_language: Record<string, number>;
  avg_processing_time_seconds: number | null;
  estimated_wait_seconds: number;
}

interface QueueItem {
  id: string;
  source_type: string;
  source_id: string;
  target_language: string;
  source_title: string | null;
  status: string;
  priority: string;
  source: string;
  attempts: number;
  error_message: string | null;
  quality_score: number | null;
  queued_at: string | null;
  completed_at: string | null;
}

export default function TranslationQueuePage() {
  const router = useRouter();
  const { user, token } = useAuth();
  const [stats, setStats] = useState<QueueStats | null>(null);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [filterLanguage, setFilterLanguage] = useState<string>("");
  const [processing, setProcessing] = useState(false);

  // Declared before the effect that calls it (block-scoped: using it in the
  // effect above would be a use-before-declaration error).
  const loadData = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const [statsData, itemsData] = await Promise.all([
        getTranslationQueueStats(token),
        listTranslationQueue(token, {
          status: filterStatus || undefined,
          target_language: filterLanguage || undefined,
          limit: 100,
        }),
      ]);
      setStats(statsData);
      setItems(itemsData);
    } catch (err) {
      console.error("Failed to load queue:", err);
    } finally {
      setLoading(false);
    }
  }, [token, filterStatus, filterLanguage]);

  useEffect(() => {
    if (!token) {
      router.push("/login");
      return;
    }
    queueMicrotask(() => loadData());
  }, [token, filterStatus, filterLanguage, loadData, router]);

  const handleRetryFailed = async () => {
    if (!token) return;
    try {
      const result = await retryFailedTranslations(token);
      alert(`Retried ${result.retried} failed items`);
      loadData();
    } catch (err) {
      alert(`Failed: ${err instanceof Error ? err.message : "unknown error"}`);
    }
  };

  const handleProcess = async () => {
    if (!token) return;
    setProcessing(true);
    try {
      const result = await processTranslationQueue(token, user?.id);
      if (result.status === "empty") {
        alert("No items in queue to process");
      } else {
        alert(`Processing: ${result.target_language} translation`);
        loadData();
      }
    } catch (err) {
      alert(`Failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setProcessing(false);
    }
  };

  const handleCancel = async (itemId: string) => {
    if (!token || !confirm("Cancel this translation?")) return;
    try {
      await cancelTranslation(token, itemId);
      loadData();
    } catch (err) {
      alert(`Failed: ${err instanceof Error ? err.message : "unknown error"}`);
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "completed":
        return "text-green-600 bg-green-50";
      case "processing":
        return "text-blue-600 bg-blue-50";
      case "pending":
        return "text-yellow-600 bg-yellow-50";
      case "failed":
        return "text-red-600 bg-red-50";
      case "retry":
        return "text-orange-600 bg-orange-50";
      case "cancelled":
        return "text-gray-600 bg-gray-50";
      default:
        return "text-gray-600 bg-gray-50";
    }
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case "urgent":
        return "text-red-700 bg-red-100";
      case "high":
        return "text-orange-700 bg-orange-100";
      case "normal":
        return "text-blue-700 bg-blue-100";
      case "low":
        return "text-gray-700 bg-gray-100";
      default:
        return "text-gray-700 bg-gray-100";
    }
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

  const formatWaitTime = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${Math.round(seconds / 3600)}h`;
  };

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">🔄 Translation Queue</h1>
          <p className="text-gray-500 mt-1">Manage automated translation tasks</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleProcess}
            disabled={processing}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {processing ? "Processing..." : "▶️ Process Next"}
          </button>
          <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
            ← Back
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4 mb-6">
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold">{stats.total}</div>
            <div className="text-sm text-gray-500">Total Tasks</div>
          </div>
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold text-yellow-600">
              {stats.by_status.pending || 0}
            </div>
            <div className="text-sm text-gray-500">Pending</div>
          </div>
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold text-blue-600">
              {stats.by_status.processing || 0}
            </div>
            <div className="text-sm text-gray-500">Processing</div>
          </div>
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold text-green-600">
              {stats.by_status.completed || 0}
            </div>
            <div className="text-sm text-gray-500">Completed</div>
          </div>
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold text-red-600">
              {stats.by_status.failed || 0}
            </div>
            <div className="text-sm text-gray-500">Failed</div>
          </div>
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold text-purple-600">
              {formatWaitTime(stats.estimated_wait_seconds)}
            </div>
            <div className="text-sm text-gray-500">Est. Wait</div>
          </div>
        </div>
      )}

      {/* Language Distribution */}
      {stats && Object.keys(stats.by_language).length > 0 && (
        <div className="bg-white rounded-lg border p-4 mb-6">
          <h3 className="font-medium mb-3">Queue by Language</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(stats.by_language).map(([lang, count]) => (
              <button
                key={lang}
                onClick={() => setFilterLanguage(filterLanguage === lang ? "" : lang)}
                className={`px-3 py-1.5 rounded text-sm ${
                  filterLanguage === lang
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 hover:bg-gray-200"
                }`}
              >
                {getLanguageName(lang)} ({count})
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="bg-white rounded-lg border p-4 mb-6">
        <div className="flex items-center gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Status</label>
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="border rounded px-3 py-1.5 text-sm"
            >
              <option value="">All</option>
              <option value="pending">Pending</option>
              <option value="processing">Processing</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="retry">Retry</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Language</label>
            <select
              value={filterLanguage}
              onChange={(e) => setFilterLanguage(e.target.value)}
              className="border rounded px-3 py-1.5 text-sm"
            >
              <option value="">All</option>
              <option value="si">Sinhala</option>
              <option value="ta">Tamil</option>
              <option value="zh">Chinese</option>
              <option value="ar">Arabic</option>
              <option value="hi">Hindi</option>
              <option value="ja">Japanese</option>
              <option value="de">German</option>
            </select>
          </div>
          <div className="flex-1" />
          {(filterStatus || filterLanguage) && (
            <button
              onClick={() => {
                setFilterStatus("");
                setFilterLanguage("");
              }}
              className="text-sm text-blue-600 hover:underline"
            >
              Clear filters
            </button>
          )}
          {(stats?.by_status.failed || 0) > 0 && (
            <button
              onClick={handleRetryFailed}
              className="bg-orange-600 text-white px-4 py-1.5 rounded text-sm hover:bg-orange-700"
            >
              🔄 Retry Failed ({stats?.by_status.failed || 0})
            </button>
          )}
        </div>
      </div>

      {/* Queue Items */}
      <div className="bg-white rounded-lg border">
        <div className="p-4 border-b flex items-center justify-between">
          <h2 className="font-semibold">
            Queue Items ({items.length})
          </h2>
          <button onClick={loadData} className="text-sm text-blue-600 hover:underline">
            🔄 Refresh
          </button>
        </div>

        {loading ? (
          <div className="p-8 text-center text-gray-500">Loading...</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No items in queue
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left py-3 px-4">Language</th>
                  <th className="text-left py-3 px-4">Source</th>
                  <th className="text-left py-3 px-4">Title</th>
                  <th className="text-left py-3 px-4">Status</th>
                  <th className="text-left py-3 px-4">Priority</th>
                  <th className="text-left py-3 px-4">Attempts</th>
                  <th className="text-left py-3 px-4">Queued</th>
                  <th className="text-left py-3 px-4">Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="border-b hover:bg-gray-50">
                    <td className="py-3 px-4">
                      <div className="font-medium">{getLanguageName(item.target_language)}</div>
                      <div className="text-xs text-gray-500">{item.target_language}</div>
                    </td>
                    <td className="py-3 px-4">
                      <span className="text-xs px-2 py-0.5 bg-gray-100 rounded">
                        {item.source_type}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-gray-700 max-w-xs truncate">
                      {item.source_title || "—"}
                    </td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-1 rounded text-xs ${getStatusColor(item.status)}`}>
                        {item.status}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-1 rounded text-xs ${getPriorityColor(item.priority)}`}>
                        {item.priority}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-center">
                      {item.attempts}/{3}
                    </td>
                    <td className="py-3 px-4 text-gray-500">
                      {item.queued_at
                        ? new Date(item.queued_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-2">
                        {item.status === "pending" && (
                          <button
                            onClick={() => handleCancel(item.id)}
                            className="text-xs text-red-600 hover:underline"
                          >
                            Cancel
                          </button>
                        )}
                        {item.status === "failed" && (
                          <span className="text-xs text-red-500 truncate max-w-[150px]" title={item.error_message || ""}>
                            {item.error_message || "Error"}
                          </span>
                        )}
                        {item.status === "completed" && item.quality_score && (
                          <span className="text-xs text-green-600">
                            Quality: {Math.round(item.quality_score * 100)}%
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
