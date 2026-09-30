"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import {
  listNotifications,
  getUnreadNotificationCount,
  markAllNotificationsRead,
} from "@/lib/api";

interface NotificationItem {
  id: string;
  notification_type: string;
  to_email: string;
  subject: string;
  status: string;
  message_id: string | null;
  sent_at: string | null;
  read_at: string | null;
  created_at: string;
}

const statusStyles: Record<string, string> = {
  sent: "bg-emerald-50 text-emerald-700 border-emerald-200",
  pending: "bg-amber-50 text-amber-700 border-amber-200",
  failed: "bg-rose-50 text-rose-700 border-rose-200",
};

function formatWhen(value: string | null) {
  if (!value) return "—";
  const d = new Date(value);
  const diffMs = Date.now() - d.getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function NotificationsPage() {
  const { token } = useAuth();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [showUnreadOnly, setShowUnreadOnly] = useState(false);
  const [marking, setMarking] = useState(false);

  const reload = useCallback(() => {
    if (!token) return;
    setError("");
    listNotifications(token, { limit: 100 })
      .then(setItems)
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Failed to load notifications")
      )
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  const types = Array.from(new Set(items.map((n) => n.notification_type))).sort();

  const filtered = items.filter((n) => {
    const matchesType = typeFilter === "all" || n.notification_type === typeFilter;
    const matchesRead = !showUnreadOnly || n.read_at === null;
    return matchesType && matchesRead;
  });

  const unreadCount = items.filter((n) => n.read_at === null).length;

  const handleMarkAllRead = async () => {
    if (!token) return;
    setMarking(true);
    setNotice("");
    try {
      const r = await markAllNotificationsRead(token);
      setNotice(
        r.marked > 0
          ? `Marked ${r.marked} notification(s) as read`
          : "Nothing unread"
      );
      reload();
      // Keep the bell badge in sync on the way back.
      getUnreadNotificationCount(token).catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to mark as read");
    } finally {
      setMarking(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Notifications</h1>
          <p className="mt-1 text-sm text-gray-500">
            Your full notification history — read and unread.
          </p>
        </div>
        <button
          onClick={handleMarkAllRead}
          disabled={marking || unreadCount === 0}
          className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
        >
          {marking ? "Marking…" : `Mark all read${unreadCount ? ` (${unreadCount})` : ""}`}
        </button>
      </div>

      {error && (
        <div className="mb-6 bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded text-sm">
          {error}
        </div>
      )}
      {notice && (
        <div className="mb-6 bg-emerald-50 border border-emerald-200 text-emerald-700 px-4 py-3 rounded text-sm">
          {notice}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
        >
          <option value="all">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input
            type="checkbox"
            checked={showUnreadOnly}
            onChange={(e) => setShowUnreadOnly(e.target.checked)}
            className="h-4 w-4 text-blue-600 border-gray-300 rounded"
          />
          Unread only
        </label>
        <span className="text-sm text-gray-500">
          {filtered.length} of {items.length}
        </span>
      </div>

      {/* List */}
      <div className="bg-white shadow rounded-lg divide-y divide-gray-100 overflow-hidden">
        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            {items.length === 0
              ? "No notifications yet."
              : "No notifications match your filters."}
          </div>
        ) : (
          filtered.map((n) => {
            const unread = n.read_at === null;
            return (
              <div
                key={n.id}
                className={`p-4 flex items-start justify-between gap-4 ${
                  unread ? "bg-blue-50/50" : ""
                }`}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    {!unread && (
                      <span
                        className="w-2 h-2 rounded-full shrink-0"
                        aria-hidden
                      />
                    )}
                    {unread && (
                      <span
                        className="w-2 h-2 rounded-full bg-blue-600 shrink-0"
                        aria-label="Unread"
                      />
                    )}
                    <span className="text-sm font-medium text-gray-900 truncate">
                      {n.subject || "(no subject)"}
                    </span>
                    <span className="text-[10px] uppercase tracking-wide text-gray-400 bg-gray-100 rounded px-1.5 py-0.5">
                      {n.notification_type.replace(/_/g, " ")}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-gray-400">
                    To {n.to_email} · {formatWhen(n.created_at)}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span
                    className={`px-2 py-0.5 text-[10px] font-medium rounded-full border ${
                      statusStyles[n.status] ??
                      "bg-gray-50 text-gray-600 border-gray-200"
                    }`}
                  >
                    {n.status}
                  </span>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                      unread
                        ? "bg-blue-100 text-blue-700 font-medium"
                        : "bg-gray-100 text-gray-400"
                    }`}
                  >
                    {unread ? "unread" : "read"}
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
