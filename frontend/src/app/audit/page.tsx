"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listAgreements,
  getAuditTrail,
  AuditEvent,
  listAuditEvidence,
  AuditEvidence,
  createAuditEvidence,
  verifyAuditChain,
  exportAuditHistory,
} from "@/lib/api";

interface Agreement {
  id: string;
  title: string;
  status: string;
  created_at: string;
}

export default function AuditPage() {
  const { token } = useAuth();
  const [agreements, setAgreements] = useState<Agreement[]>([]);
  const [agreementId, setAgreementId] = useState<string>("");
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [evidence, setEvidence] = useState<AuditEvidence[]>([]);
  const [verification, setVerification] = useState<{
    valid: boolean;
    checked: number;
    message: string;
  } | null>(null);
  const [exportData, setExportData] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Evidence form
  const [evType, setEvType] = useState("signed_document");
  const [evHash, setEvHash] = useState("");
  const [evContentRef, setEvContentRef] = useState("");

  useEffect(() => {
    if (token) {
      listAgreements(token).then(setAgreements).catch(console.error);
    }
  }, [token]);

  const reload = useCallback(() => {
    if (!token || !agreementId) return;
    setError(null);
    Promise.all([
      getAuditTrail(token, agreementId),
      listAuditEvidence(token, agreementId),
    ])
      .then(([evts, evs]) => {
        setEvents(evts);
        setEvidence(evs);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load audit data"))
      .finally(() => setLoading(false));
  }, [token, agreementId]);

  useEffect(() => {
    if (token && agreementId) {
      // All state updates inside `reload` happen in async promise callbacks;
      // deferring the call keeps the effect body itself setState-free.
      queueMicrotask(() => reload());
    } else {
      queueMicrotask(() => {
        setEvents([]);
        setEvidence([]);
      });
    }
  }, [token, agreementId, reload]);

  const handleVerify = async () => {
    if (!token || !agreementId) return;
    setError(null);
    try {
      const result = await verifyAuditChain(token, agreementId);
      setVerification({
        valid: result.valid,
        checked: result.checked,
        message: result.message,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Verification failed");
    }
  };

  const handleExport = async () => {
    if (!token || !agreementId) return;
    setError(null);
    try {
      const data = await exportAuditHistory(token, agreementId);
      setExportData(JSON.stringify(data, null, 2));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    }
  };

  const handleAddEvidence = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !agreementId || !evHash.trim()) return;
    setError(null);
    try {
      await createAuditEvidence(token, agreementId, {
        evidence_type: evType,
        content_hash: evHash.trim(),
        content_ref: evContentRef.trim() || undefined,
      });
      setEvHash("");
      setEvContentRef("");
      await listAuditEvidence(token, agreementId).then(setEvidence);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to capture evidence");
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
              ← Dashboard
            </Link>
            <h1 className="text-2xl font-bold text-gray-900 mt-1">
              Audit Trail &amp; Evidence
            </h1>
            <p className="text-sm text-gray-500 mt-1">
              Tamper-evident hash chain (spec 1.20) — events are immutable; verify the chain and
              capture evidence snapshots.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleVerify}
              disabled={!agreementId || loading}
              className="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700 disabled:opacity-50"
            >
              ✓ Verify Chain
            </button>
            <button
              onClick={handleExport}
              disabled={!agreementId}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
            >
              📥 Export
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-md bg-rose-50 border border-rose-200 text-sm text-rose-700">
            {error}
          </div>
        )}

        {verification && (
          <div
            className={`mb-6 px-4 py-3 rounded-md border text-sm ${
              verification.valid
                ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                : "bg-rose-50 border-rose-200 text-rose-800"
            }`}
          >
            <strong>{verification.valid ? "Chain verified" : "Chain broken!"}</strong> —{" "}
            {verification.message} ({verification.checked} events checked)
          </div>
        )}

        {/* Agreement selector */}
        <div className="bg-white shadow rounded-lg p-4 mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Agreement
          </label>
          <select
            value={agreementId}
            onChange={(e) => setAgreementId(e.target.value)}
            className="block w-full max-w-md rounded-md border-gray-300 shadow-sm focus:border-green-500 focus:ring-green-500 text-sm"
          >
            <option value="">Select an agreement…</option>
            {agreements.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title} ({a.status})
              </option>
            ))}
          </select>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Events */}
          <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200">
              <h2 className="text-lg font-medium text-gray-900">
                Audit Events ({events.length})
              </h2>
            </div>
            {events.length === 0 ? (
              <p className="px-6 py-12 text-sm text-gray-500 text-center">
                No audit events for this agreement yet.
              </p>
            ) : (
              <div className="overflow-x-auto max-h-[32rem] overflow-y-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">#</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Action</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Actor</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">When</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Event Hash</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {events.map((e) => (
                      <tr key={e.id} className="hover:bg-gray-50">
                        <td className="px-6 py-3 text-xs font-mono text-gray-400">
                          {e.sequence_number ?? "—"}
                        </td>
                        <td className="px-6 py-3 text-sm font-medium text-gray-900">
                          {e.action}
                          <div className="text-xs text-gray-500 mt-0.5">
                            {e.resource_type ?? ""}
                            {e.resource_id ? ` / ${e.resource_id.slice(0, 8)}` : ""}
                          </div>
                        </td>
                        <td className="px-6 py-3 text-xs text-gray-600">
                          {e.actor_type}
                          {e.actor_id ? ` / ${e.actor_id.slice(0, 8)}` : ""}
                        </td>
                        <td className="px-6 py-3 text-xs text-gray-500">
                          {new Date(e.created_at).toLocaleString()}
                        </td>
                        <td className="px-6 py-3">
                          <span className="font-mono text-[11px] text-gray-500" title={e.event_hash ?? ""}>
                            {e.event_hash ? e.event_hash.slice(0, 16) + "…" : "—"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Evidence */}
          <div className="bg-white shadow rounded-lg overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200">
              <h2 className="text-lg font-medium text-gray-900">
                Evidence Snapshots ({evidence.length})
              </h2>
            </div>
            <div className="p-6 border-b border-gray-200">
              <form onSubmit={handleAddEvidence} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Type</label>
                  <select
                    value={evType}
                    onChange={(e) => setEvType(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  >
                    <option value="signed_document">signed_document</option>
                    <option value="version_snapshot">version_snapshot</option>
                    <option value="sealed_package">sealed_package</option>
                    <option value="legal_hold">legal_hold</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Content hash *
                  </label>
                  <input
                    value={evHash}
                    onChange={(e) => setEvHash(e.target.value)}
                    placeholder="sha256:…"
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm font-mono"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Content ref</label>
                  <input
                    value={evContentRef}
                    onChange={(e) => setEvContentRef(e.target.value)}
                    placeholder="object key / URL"
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  />
                </div>
                <button
                  type="submit"
                  disabled={!agreementId}
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700 disabled:opacity-50"
                >
                  Capture evidence
                </button>
              </form>
            </div>
            {evidence.length === 0 ? (
              <p className="px-6 py-8 text-sm text-gray-500 text-center">No evidence yet.</p>
            ) : (
              <ul className="divide-y divide-gray-200 max-h-80 overflow-y-auto">
                {evidence.map((ev) => (
                  <li key={ev.id} className="px-6 py-3">
                    <div className="text-sm font-medium text-gray-900">{ev.evidence_type}</div>
                    <div className="text-xs font-mono text-gray-500 mt-0.5" title={ev.content_hash}>
                      {ev.content_hash.slice(0, 24)}…
                    </div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {new Date(ev.created_at).toLocaleString()}
                      {ev.content_ref ? ` · ${ev.content_ref}` : ""}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {exportData && (
          <div className="mt-6 bg-white shadow rounded-lg overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
              <h2 className="text-lg font-medium text-gray-900">Exported History</h2>
              <button
                onClick={() => {
                  const blob = new Blob([exportData], { type: "application/json" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `audit-${agreementId.slice(0, 8)}.json`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
                className="px-3 py-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
              >
                ⬇ Download JSON
              </button>
            </div>
            <pre className="px-6 py-4 text-xs text-gray-700 max-h-96 overflow-auto whitespace-pre-wrap break-all">
              {exportData}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}