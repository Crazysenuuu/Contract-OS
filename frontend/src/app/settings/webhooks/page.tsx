"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listWebhooks,
  createWebhook,
  deleteWebhook,
  testWebhook,
  getWebhookDeliveries,
  getWebhookEvents,
} from "@/lib/api";

type Webhook = Awaited<ReturnType<typeof listWebhooks>>[number];
type WebhookDelivery = Awaited<
  ReturnType<typeof getWebhookDeliveries>
>[number];

export default function WebhooksSettingsPage() {
  const { token } = useAuth();
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Create form
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [secret, setSecret] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<string[]>([]);

  // Deliveries drawer
  const [openWebhook, setOpenWebhook] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<Record<string, WebhookDelivery[]>>({});

  const reload = useCallback(() => {
    if (!token) return;
    setError(null);
    Promise.all([listWebhooks(token), getWebhookEvents()])
      .then(([hooks, ev]) => {
        setWebhooks(hooks);
        setEvents(ev.events);
      })
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Failed to load webhooks")
      );
  }, [token]);

  useEffect(() => {
    void Promise.resolve().then(reload);
  }, [reload]);

  const handleCreate = async () => {
    if (!token || !url) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await createWebhook(token, {
        url,
        description: description || undefined,
        events: selectedEvents.length ? selectedEvents : undefined,
        secret: secret || undefined,
      });
      setNotice("Webhook created");
      setUrl("");
      setDescription("");
      setSecret("");
      setSelectedEvents([]);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create webhook");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await deleteWebhook(token, id);
      setNotice("Webhook deleted");
      if (openWebhook === id) setOpenWebhook(null);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete webhook");
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async (id: string) => {
    if (!token) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await testWebhook(token, id);
      setNotice(`Test event queued (${res.deliveries_queued} delivery attempt)`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to test webhook");
    } finally {
      setBusy(false);
    }
  };

  const toggleDeliveries = async (id: string) => {
    if (!token) return;
    if (openWebhook === id) {
      setOpenWebhook(null);
      return;
    }
    setOpenWebhook(id);
    try {
      const d = await getWebhookDeliveries(token, id);
      setDeliveries((prev) => ({ ...prev, [id]: d }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load deliveries");
    }
  };

  const toggleEvent = (ev: string) => {
    setSelectedEvents((prev) =>
      prev.includes(ev) ? prev.filter((x) => x !== ev) : [...prev, ev]
    );
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <main className="max-w-5xl mx-auto px-4 py-8">
        <div className="mb-6">
          <Link
            href="/settings"
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            ← Settings
          </Link>
          <h1 className="mt-2 text-2xl font-bold text-gray-900">Webhooks</h1>
          <p className="mt-1 text-sm text-gray-600">
            Register HTTP endpoints to receive agreement events in real time.
          </p>
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

        {/* Create form */}
        <div className="mb-8 bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">
            Add webhook
          </h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="sm:col-span-2">
              <span className="block text-xs font-medium text-gray-700 mb-1">
                Endpoint URL (HTTPS)
              </span>
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/hooks/agreements"
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
            <label>
              <span className="block text-xs font-medium text-gray-700 mb-1">
                Description (optional)
              </span>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
            <label>
              <span className="block text-xs font-medium text-gray-700 mb-1">
                Signing secret (optional)
              </span>
              <input
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                type="password"
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
          </div>
          {events.length > 0 && (
            <div className="mt-4">
              <span className="block text-xs font-medium text-gray-700 mb-2">
                Events ({selectedEvents.length || "all"} selected)
              </span>
              <div className="flex flex-wrap gap-2">
                {events.map((ev) => (
                  <button
                    key={ev}
                    type="button"
                    onClick={() => toggleEvent(ev)}
                    className={`px-3 py-1 text-xs rounded-full border ${
                      selectedEvents.includes(ev)
                        ? "bg-blue-600 text-white border-blue-600"
                        : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                    }`}
                  >
                    {ev}
                  </button>
                ))}
              </div>
            </div>
          )}
          <button
            onClick={handleCreate}
            disabled={busy || !url}
            className="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            Create webhook
          </button>
        </div>

        {/* List */}
        <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
          {webhooks.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">
              No webhooks registered yet.
            </p>
          ) : (
            webhooks.map((w) => (
              <div key={w.id} className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-block w-2 h-2 rounded-full ${
                          w.is_active ? "bg-emerald-500" : "bg-gray-300"
                        }`}
                      />
                      <span className="text-sm font-medium text-gray-900 truncate">
                        {w.url}
                      </span>
                    </div>
                    {w.description && (
                      <p className="mt-0.5 text-xs text-gray-500">
                        {w.description}
                      </p>
                    )}
                    {w.events && w.events.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {w.events.map((ev) => (
                          <span
                            key={ev}
                            className="px-2 py-0.5 text-[10px] rounded-full bg-gray-100 text-gray-600"
                          >
                            {ev}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => toggleDeliveries(w.id)}
                      className="px-3 py-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
                    >
                      {openWebhook === w.id ? "Hide deliveries" : "Deliveries"}
                    </button>
                    <button
                      onClick={() => handleTest(w.id)}
                      disabled={busy}
                      className="px-3 py-1.5 text-xs font-medium text-white bg-gray-800 rounded-md hover:bg-gray-900 disabled:opacity-50"
                    >
                      Test
                    </button>
                    <button
                      onClick={() => handleDelete(w.id)}
                      disabled={busy}
                      className="px-3 py-1.5 text-xs font-medium text-white bg-rose-600 rounded-md hover:bg-rose-700 disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {openWebhook === w.id && (
                  <div className="mt-3 overflow-x-auto">
                    <table className="min-w-full text-xs">
                      <thead>
                        <tr className="text-left text-gray-500 border-b border-gray-100">
                          <th className="py-2 pr-4 font-medium">Event</th>
                          <th className="py-2 pr-4 font-medium">Status</th>
                          <th className="py-2 pr-4 font-medium">HTTP</th>
                          <th className="py-2 pr-4 font-medium">Attempt</th>
                          <th className="py-2 pr-4 font-medium">When</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50">
                        {(deliveries[w.id] ?? []).map((d) => (
                          <tr key={d.id}>
                            <td className="py-2 pr-4 text-gray-900">
                              {d.event_type}
                            </td>
                            <td className="py-2 pr-4">
                              <span
                                className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
                                  d.status === "delivered"
                                    ? "bg-emerald-50 text-emerald-700"
                                    : d.status === "failed"
                                      ? "bg-rose-50 text-rose-700"
                                      : "bg-amber-50 text-amber-700"
                                }`}
                              >
                                {d.status}
                              </span>
                            </td>
                            <td className="py-2 pr-4 text-gray-600">
                              {d.response_status_code ?? "—"}
                            </td>
                            <td className="py-2 pr-4 text-gray-600">
                              {d.attempt}
                            </td>
                            <td className="py-2 pr-4 text-gray-500">
                              {new Date(d.created_at).toLocaleString()}
                            </td>
                          </tr>
                        ))}
                        {deliveries[w.id]?.length === 0 && (
                          <tr>
                            <td
                              colSpan={5}
                              className="py-3 text-gray-400 italic"
                            >
                              No deliveries recorded yet — use Test to send one.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </main>
    </div>
  );
}
