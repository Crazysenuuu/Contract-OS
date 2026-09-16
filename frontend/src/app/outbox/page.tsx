"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { useNotificationSocket } from "@/hooks/useNotificationSocket";
import {
  getOutboxStats,
  listOutboxEvents,
  OutboxEvent,
  enqueueOutboxEvent,
  processOutboxEvents,
} from "@/lib/api";

interface LiveMessage {
  id: number;
  ts: string;
  data: Record<string, unknown>;
}

export default function OutboxPage() {
  const { token, user } = useAuth();
  const isAdmin = !!user?.is_admin;
  const [stats, setStats] = useState<{ pending: number; published: number; failed: number; total: number } | null>(null);
  const [events, setEvents] = useState<OutboxEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Enqueue form
  const [evType, setEvType] = useState("agreement.status_changed");
  const [evAggregate, setEvAggregate] = useState("agreement");
  const [evPayload, setEvPayload] = useState(
    JSON.stringify({ subject: "Agreement status changed" }, null, 2)
  );

  // WebSocket (auto-connect while token available, reconnect with backoff)
  const [liveMessages, setLiveMessages] = useState<LiveMessage[]>([]);
  const msgId = useRef(0);
  const { state: wsState, reconnectCount, wsRef } = useNotificationSocket(
    token,
    (data) => {
      setLiveMessages((prev) =>
        [{ id: ++msgId.current, ts: new Date().toISOString(), data }, ...prev].slice(0, 25)
      );
    }
  );

  const reload = useCallback(() => {
    if (!token) return;
    setError(null);
    Promise.all([getOutboxStats(token), listOutboxEvents(token)])
      .then(([st, ev]) => {
        setStats(st);
        setEvents(ev);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load outbox data"));
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);


  const run = async (fn: () => Promise<unknown>, successMsg: string, after?: () => void) => {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(successMsg);
      after?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    }
  };

  const handleEnqueue = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !evAggregate.trim()) return;
    let payload: Record<string, unknown> | undefined;
    if (evPayload.trim()) {
      try {
        payload = JSON.parse(evPayload);
      } catch {
        setError("Payload must be valid JSON");
        return;
      }
    }
    run(
      () =>
        enqueueOutboxEvent(token, {
          event_type: evType,
          aggregate_type: evAggregate.trim(),
          payload,
        }),
      "Event enqueued",
      reload
    );
  };

  const handleProcess = () => {
    if (!token) return;
    run(
      () => processOutboxEvents(token),
      "Pending events processed",
      reload
    );
  };

  const sendPing = () => {
    wsRef.current?.send("ping");
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
              ← Dashboard
            </Link>
            <h1 className="text-2xl font-bold text-gray-900 mt-1">Event Outbox &amp; Real-Time</h1>
            <p className="text-sm text-gray-500 mt-1">
              Outbox events are flushed on an interval by the scheduler; connected clients receive
              live pushes over WebSocket (spec 1.14).
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 px-3 py-1.5 bg-white border border-gray-300 rounded-md">
              <span
                className={`w-2.5 h-2.5 rounded-full ${
                  wsState === "open"
                    ? "bg-emerald-500 animate-pulse"
                    : wsState === "connecting"
                      ? "bg-amber-500"
                      : "bg-gray-400"
                }`}
              />
              <span className="text-xs font-medium text-gray-700">
                {wsState === "open"
                  ? "Live"
                  : wsState === "connecting"
                    ? "Connecting…"
                    : reconnectCount > 0
                      ? `Reconnecting… (${reconnectCount})`
                      : "Disconnected"}
              </span>
            </div>
            <button
              onClick={sendPing}
              disabled={wsState !== "open"}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
            >
              Ping
            </button>
            {isAdmin && (
              <button
                onClick={handleProcess}
                className="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700"
              >
                ▶ Process pending
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-md bg-rose-50 border border-rose-200 text-sm text-rose-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-4 px-4 py-3 rounded-md bg-emerald-50 border border-emerald-200 text-sm text-emerald-800">
            {notice}
          </div>
        )}

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {[
            { label: "Pending", value: stats?.pending ?? 0, color: "text-amber-600" },
            { label: "Published", value: stats?.published ?? 0, color: "text-emerald-600" },
            { label: "Failed", value: stats?.failed ?? 0, color: "text-rose-600" },
            { label: "Total", value: stats?.total ?? 0, color: "text-gray-900" },
          ].map((s) => (
            <div key={s.label} className="bg-white shadow rounded-lg p-5">
              <div className="text-xs text-gray-500 uppercase tracking-wider">{s.label}</div>
              <div className={`text-3xl font-extrabold mt-1 ${s.color}`}>{s.value}</div>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Events table */}
          <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200">
              <h2 className="text-lg font-medium text-gray-900">Recent Events ({events.length})</h2>
            </div>
            {events.length === 0 ? (
              <p className="px-6 py-12 text-sm text-gray-500 text-center">
                No outbox events yet. Enqueue one on the right.
              </p>
            ) : (
              <div className="overflow-x-auto max-h-[30rem] overflow-y-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Event</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Aggregate</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Attempts</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Created</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {events.map((ev) => (
                      <tr key={ev.id} className="hover:bg-gray-50">
                        <td className="px-6 py-3">
                          <div className="text-sm font-medium text-gray-900">{ev.event_type}</div>
                          {ev.last_error && (
                            <div className="text-[11px] text-rose-600 mt-0.5 max-w-xs truncate" title={ev.last_error}>
                              {ev.last_error}
                            </div>
                          )}
                        </td>
                        <td className="px-6 py-3 text-xs text-gray-500">
                          {ev.aggregate_type}
                          {ev.aggregate_id ? ` / ${ev.aggregate_id.slice(0, 8)}` : ""}
                        </td>
                        <td className="px-6 py-3">
                          <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border ${
                            ev.status === "published"
                              ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                              : ev.status === "failed"
                                ? "bg-rose-100 text-rose-800 border-rose-200"
                                : "bg-amber-100 text-amber-800 border-amber-200"
                          }`}>
                            {ev.status}
                          </span>
                        </td>
                        <td className="px-6 py-3 text-xs text-gray-500">{ev.attempts}</td>
                        <td className="px-6 py-3 text-xs text-gray-500">
                          {new Date(ev.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Enqueue + live */}
          <div className="space-y-6">
            <div className="bg-white shadow rounded-lg p-6">
              <h2 className="text-lg font-medium text-gray-900 mb-4">Enqueue Event</h2>
              <form onSubmit={handleEnqueue} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Event type</label>
                  <select
                    value={evType}
                    onChange={(e) => setEvType(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  >
                    <option value="agreement.status_changed">agreement.status_changed</option>
                    <option value="negotiation.proposal_created">negotiation.proposal_created</option>
                    <option value="negotiation.proposal_accepted">negotiation.proposal_accepted</option>
                    <option value="signature.completed">signature.completed</option>
                    <option value="approval.required">approval.required</option>
                    <option value="obligation.reminder">obligation.reminder</option>
                    <option value="compliance.violation">compliance.violation</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Aggregate type *</label>
                  <input
                    value={evAggregate}
                    onChange={(e) => setEvAggregate(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Payload (JSON)</label>
                  <textarea
                    value={evPayload}
                    onChange={(e) => setEvPayload(e.target.value)}
                    rows={5}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm font-mono"
                  />
                  <p className="mt-1 text-[11px] text-gray-400">
                    Include recipient_email / recipient_user_id to also create a notification.
                  </p>
                </div>
                <button
                  type="submit"
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700"
                >
                  Enqueue
                </button>
              </form>
            </div>

            <div className="bg-white shadow rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-medium text-gray-900">Live Pushes</h2>
              </div>
              {liveMessages.length === 0 ? (
                <p className="px-6 py-8 text-sm text-gray-500 text-center">
                  No real-time messages yet. Process pending events or ping the socket.
                </p>
              ) : (
                <ul className="divide-y divide-gray-200 max-h-72 overflow-y-auto">
                  {liveMessages.map((m) => (
                    <li key={m.id} className="px-6 py-3">
                      <div className="text-[11px] text-gray-400">{new Date(m.ts).toLocaleTimeString()}</div>
                      <div className="text-xs font-mono text-gray-700 mt-0.5 break-all">
                        {JSON.stringify(m.data)}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}