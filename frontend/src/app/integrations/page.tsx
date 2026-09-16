"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import {
  IntegrationConnectorRow,
  createIntegrationConnector,
  listIntegrationConnectors,
  listIntegrationProviders,
  testIntegrationConnector,
} from "@/lib/api";

export default function IntegrationsPage() {
  const { token } = useAuth();

  const [providers, setProviders] = useState<Array<{ key: string; label: string }>>([]);
  const [supportedEvents, setSupportedEvents] = useState<string[]>([]);
  const [connectors, setConnectors] = useState<IntegrationConnectorRow[]>([]);
  const [loading, setLoading] = useState(true);

  // Create form
  const [provider, setProvider] = useState("");
  const [name, setName] = useState("");
  const [events, setEvents] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Test result
  const [testPayload, setTestPayload] = useState<{ provider: string; sample_payload: unknown } | null>(null);

  const reload = () => {
    if (!token) return;
    listIntegrationConnectors(token).then(setConnectors).catch(() => {});
  };

  useEffect(() => {
    if (!token) return;
    listIntegrationProviders(token)
      .then((res) => {
        setProviders(res.providers);
        setSupportedEvents(res.supported_events);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const toggleEvent = (e: string) => {
    setEvents((prev) =>
      prev.includes(e) ? prev.filter((x) => x !== e) : [...prev, e]
    );
  };

  const createConnector = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setError(null);
    setNotice(null);
    try {
      await createIntegrationConnector(token, {
        provider,
        name: name || undefined,
        events,
      });
      setNotice(`Connected ${provider}.`);
      setName("");
      setEvents([]);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create connector");
    }
  };

  const runTest = async (id: string) => {
    if (!token) return;
    try {
      const res = await testIntegrationConnector(token, id);
      setTestPayload(res);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Test failed");
    }
  };

  const syncBadge = (status: string | null) => {
    const map: Record<string, string> = {
      success: "bg-green-100 text-green-800",
      failed: "bg-red-100 text-red-800",
      pending: "bg-yellow-100 text-yellow-800",
    };
    return (
      <span
        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
          map[status || ""] || "bg-gray-100 text-gray-600"
        }`}
      >
        {status || "never synced"}
      </span>
    );
  };

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Integrations</h1>
        <p className="text-sm text-gray-500">
          Push contract events to your ERP / CRM / billing stack — Salesforce,
          HubSpot, NetSuite, SAP, and Stripe.
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-800">
          {notice}
        </div>
      )}

      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="font-medium text-gray-800">Add a connector</h2>
        </div>
        <form onSubmit={createConnector} className="space-y-4 p-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Provider
            </label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              required
              className="w-full sm:w-80 rounded border border-gray-300 px-3 py-2 text-sm"
            >
              <option value="">Select provider…</option>
              {providers.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Display name
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Salesforce Prod"
              className="w-full sm:w-80 rounded border border-gray-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Events to sync
            </label>
            <div className="flex flex-wrap gap-2">
              {supportedEvents.map((ev) => {
                const active = events.includes(ev);
                return (
                  <button
                    type="button"
                    key={ev}
                    onClick={() => toggleEvent(ev)}
                    className={`rounded-full px-3 py-1 text-xs border ${
                      active
                        ? "border-blue-400 bg-blue-50 text-blue-800"
                        : "border-gray-300 text-gray-600"
                    }`}
                  >
                    {ev.replace(/_/g, " ")}
                  </button>
                );
              })}
            </div>
          </div>
          <button
            type="submit"
            disabled={!provider}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Connect
          </button>
        </form>
      </section>

      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="font-medium text-gray-800">
            Connected ({connectors.length})
          </h2>
        </div>
        {!loading && connectors.length === 0 ? (
          <p className="p-4 text-sm text-gray-400">
            No connectors configured yet.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {connectors.map((c) => (
              <li key={c.id} className="flex items-center justify-between px-4 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-800">
                    {c.name}{" "}
                    <span className="text-xs text-gray-400">
                      ({c.provider_label})
                    </span>
                  </p>
                  <p className="mt-1 text-xs text-gray-500">
                    {c.enabled ? (
                      <span className="text-green-700">● enabled</span>
                    ) : (
                      <span className="text-gray-400">○ disabled</span>
                    )}
                    {" · "}
                    {c.events
                      .map((ev) => ev.replace(/_/g, " "))
                      .join(", ")}
                  </p>
                  {c.last_error && (
                    <p className="mt-1 text-xs text-red-600">{c.last_error}</p>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  {syncBadge(c.last_sync_status)}
                  <button
                    onClick={() => runTest(c.id)}
                    className="rounded border border-gray-300 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50"
                  >
                    Test
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {testPayload && (
        <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-4 py-3 flex items-center justify-between">
            <h2 className="font-medium text-gray-800">
              Test payload — {testPayload.provider}
            </h2>
            <button
              onClick={() => setTestPayload(null)}
              className="text-xs text-gray-500 hover:text-gray-700"
            >
              Close
            </button>
          </div>
          <pre className="overflow-x-auto p-4 text-xs text-gray-700">
            {JSON.stringify(testPayload.sample_payload, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
}