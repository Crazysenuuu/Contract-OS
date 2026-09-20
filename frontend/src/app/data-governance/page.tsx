"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useRequireAuth } from "@/hooks/useRequireAuth";
import {
  listRetentionPolicies,
  createRetentionPolicy,
  updateRetentionPolicy,
  listLegalHolds,
  createLegalHold,
  releaseLegalHold,
  listErasureRequests,
  createErasureRequest,
  executeErasureRequest,
} from "@/lib/api";

interface RetentionPolicy {
  id: string;
  name: string;
  description: string | null;
  scope: string;
  agreement_type_key: string | null;
  retention_months: number;
  disposition: string;
  is_active: boolean;
  created_at: string;
}

interface LegalHold {
  id: string;
  agreement_id: string | null;
  reason: string;
  hold_type: string;
  released_at: string | null;
  created_at: string;
}

interface ErasureRequest {
  id: string;
  data_subject: string;
  regulation: string;
  status: string;
  agreement_id: string | null;
  shredded_fields: string[] | null;
  completed_at: string | null;
}

const statusBadge: Record<string, string> = {
  active: "bg-green-100 text-green-800",
  pending: "bg-yellow-100 text-yellow-800",
  completed: "bg-blue-100 text-blue-800",
  executed: "bg-blue-100 text-blue-800",
};

export default function DataGovernancePage() {
  const { token } = useAuth();
  useRequireAuth();
  const router = useRouter();
  const [policies, setPolicies] = useState<RetentionPolicy[]>([]);
  const [holds, setHolds] = useState<LegalHold[]>([]);
  const [erasures, setErasures] = useState<ErasureRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Policy form
  const [showPolicyForm, setShowPolicyForm] = useState(false);
  const [policyForm, setPolicyForm] = useState({
    name: "",
    scope: "all",
    agreement_type_key: "",
    retention_months: 84,
    disposition: "archive",
  });

  // Hold form
  const [showHoldForm, setShowHoldForm] = useState(false);
  const [holdForm, setHoldForm] = useState({ agreement_id: "", reason: "", hold_type: "manual" });

  // Erasure form
  const [showErasureForm, setShowErasureForm] = useState(false);
  const [erasureForm, setErasureForm] = useState({ data_subject: "", agreement_id: "", regulation: "gdpr" });

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [p, h, e] = await Promise.all([
        listRetentionPolicies(token).catch(() => []),
        listLegalHolds(token).catch(() => []),
        listErasureRequests(token).catch(() => []),
      ]);
      setPolicies(p);
      setHolds(h);
      setErasures(e);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load governance data");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    queueMicrotask(() => load());
  }, [token, load]);

  const handleCreatePolicy = async () => {
    if (!token || !policyForm.name) return;
    setBusy(true);
    try {
      await createRetentionPolicy(token, {
        name: policyForm.name,
        scope: policyForm.scope,
        agreement_type_key: policyForm.scope === "agreement_type" ? policyForm.agreement_type_key : undefined,
        retention_months: policyForm.retention_months,
        disposition: policyForm.disposition,
      });
      setShowPolicyForm(false);
      setPolicyForm({ name: "", scope: "all", agreement_type_key: "", retention_months: 84, disposition: "archive" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create policy failed");
    } finally {
      setBusy(false);
    }
  };

  const handleTogglePolicy = async (policy: RetentionPolicy) => {
    if (!token) return;
    try {
      await updateRetentionPolicy(token, policy.id, { is_active: !policy.is_active });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleCreateHold = async () => {
    if (!token || !holdForm.reason) return;
    setBusy(true);
    try {
      await createLegalHold(token, {
        agreement_id: holdForm.agreement_id || undefined,
        reason: holdForm.reason,
        hold_type: holdForm.hold_type,
      });
      setShowHoldForm(false);
      setHoldForm({ agreement_id: "", reason: "", hold_type: "manual" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create hold failed");
    } finally {
      setBusy(false);
    }
  };

  const handleReleaseHold = async (holdId: string) => {
    if (!token) return;
    try {
      await releaseLegalHold(token, holdId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Release failed");
    }
  };

  const handleCreateErasure = async () => {
    if (!token || !erasureForm.data_subject) return;
    setBusy(true);
    try {
      await createErasureRequest(token, {
        data_subject: erasureForm.data_subject,
        agreement_id: erasureForm.agreement_id || undefined,
        regulation: erasureForm.regulation,
      });
      setShowErasureForm(false);
      setErasureForm({ data_subject: "", agreement_id: "", regulation: "gdpr" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create erasure request failed");
    } finally {
      setBusy(false);
    }
  };

  const handleExecuteErasure = async (requestId: string) => {
    if (!token) return;
    setBusy(true);
    try {
      await executeErasureRequest(token, requestId, {});
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Execute erasure failed");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  return (
    <div className="max-w-7xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
      <h1 className="text-2xl font-semibold text-gray-900 mb-1">Data Governance</h1>
      <p className="text-sm text-gray-500 mb-6">
        Retention policies, legal holds, and privacy erasure requests
      </p>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="space-y-8">
        {/* Retention Policies */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-medium text-gray-900">Retention Policies</h2>
            <button
              onClick={() => setShowPolicyForm(!showPolicyForm)}
              className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              {showPolicyForm ? "Cancel" : "New policy"}
            </button>
          </div>

          {showPolicyForm && (
            <div className="bg-white shadow rounded-lg p-4 mb-4 space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <input
                  type="text"
                  placeholder="Policy name"
                  value={policyForm.name}
                  onChange={(e) => setPolicyForm({ ...policyForm, name: e.target.value })}
                  className="border border-gray-300 rounded px-3 py-2 text-sm"
                />
                <select
                  value={policyForm.scope}
                  onChange={(e) => setPolicyForm({ ...policyForm, scope: e.target.value })}
                  className="border border-gray-300 rounded px-3 py-2 text-sm"
                >
                  <option value="all">All agreement types</option>
                  <option value="agreement_type">Specific agreement type</option>
                </select>
                {policyForm.scope === "agreement_type" && (
                  <input
                    type="text"
                    placeholder="Agreement type key (e.g. nda)"
                    value={policyForm.agreement_type_key}
                    onChange={(e) => setPolicyForm({ ...policyForm, agreement_type_key: e.target.value })}
                    className="border border-gray-300 rounded px-3 py-2 text-sm"
                  />
                )}
                <div className="flex items-center gap-2">
                  <label className="text-sm text-gray-700 whitespace-nowrap">
                    Retain for
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={policyForm.retention_months}
                    onChange={(e) => setPolicyForm({ ...policyForm, retention_months: Number(e.target.value) })}
                    className="w-24 border border-gray-300 rounded px-3 py-2 text-sm"
                  />
                  <span className="text-sm text-gray-500">months</span>
                </div>
                <select
                  value={policyForm.disposition}
                  onChange={(e) => setPolicyForm({ ...policyForm, disposition: e.target.value })}
                  className="border border-gray-300 rounded px-3 py-2 text-sm"
                >
                  <option value="archive">Archive when expired</option>
                  <option value="delete">Delete when expired</option>
                </select>
              </div>
              <button
                onClick={handleCreatePolicy}
                disabled={busy || !policyForm.name || (policyForm.scope === "agreement_type" && !policyForm.agreement_type_key)}
                className="text-sm px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {busy ? "Creating..." : "Create policy"}
              </button>
            </div>
          )}

          {policies.length === 0 ? (
            <div className="bg-white shadow rounded-lg p-6 text-sm text-gray-500">
              No retention policies yet.
            </div>
          ) : (
            <div className="bg-white shadow rounded-lg divide-y divide-gray-200">
              {policies.map((policy) => (
                <div key={policy.id} data-testid={`policy-row-${policy.name}`} className="p-4 flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-900">{policy.name}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${policy.is_active ? statusBadge.active : "bg-gray-100 text-gray-600"}`}>
                        {policy.is_active ? "active" : "paused"}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      Scope: {policy.scope === "all" ? "all agreements" : policy.agreement_type_key} ·
                      retain {policy.retention_months} months · {policy.disposition} on expiry
                    </p>
                  </div>
                  <button
                    onClick={() => handleTogglePolicy(policy)}
                    className="text-xs px-3 py-1.5 border border-gray-300 rounded hover:bg-gray-50 whitespace-nowrap"
                  >
                    {policy.is_active ? "Pause" : "Resume"}
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Legal Holds */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-medium text-gray-900">Legal Holds</h2>
            <button
              onClick={() => setShowHoldForm(!showHoldForm)}
              className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              {showHoldForm ? "Cancel" : "Place hold"}
            </button>
          </div>

          {showHoldForm && (
            <div className="bg-white shadow rounded-lg p-4 mb-4 space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <input
                  type="text"
                  placeholder="Agreement ID (optional — org-wide if blank)"
                  value={holdForm.agreement_id}
                  onChange={(e) => setHoldForm({ ...holdForm, agreement_id: e.target.value })}
                  className="border border-gray-300 rounded px-3 py-2 text-sm"
                />
                <select
                  value={holdForm.hold_type}
                  onChange={(e) => setHoldForm({ ...holdForm, hold_type: e.target.value })}
                  className="border border-gray-300 rounded px-3 py-2 text-sm"
                >
                  <option value="manual">Manual</option>
                  <option value="litigation">Litigation</option>
                  <option value="investigation">Investigation</option>
                  <option value="regulatory">Regulatory</option>
                  <option value="audit">Audit</option>
                </select>
                <input
                  type="text"
                  placeholder="Reason"
                  value={holdForm.reason}
                  onChange={(e) => setHoldForm({ ...holdForm, reason: e.target.value })}
                  className="sm:col-span-2 border border-gray-300 rounded px-3 py-2 text-sm"
                />
              </div>
              <button
                onClick={handleCreateHold}
                disabled={busy || !holdForm.reason}
                className="text-sm px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {busy ? "Placing..." : "Place hold"}
              </button>
            </div>
          )}

          {holds.length === 0 ? (
            <div className="bg-white shadow rounded-lg p-6 text-sm text-gray-500">
              No active legal holds.
            </div>
          ) : (
            <div className="bg-white shadow rounded-lg divide-y divide-gray-200">
              {holds.map((hold) => (
                <div key={hold.id} className="p-4 flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-purple-100 text-purple-800 capitalize">
                        {hold.hold_type}
                      </span>
                      <span className="text-sm text-gray-900">{hold.reason}</span>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      {hold.agreement_id
                        ? `Agreement ${hold.agreement_id.slice(0, 8)}`
                        : "Organization-wide"}{" "}
                      · placed {new Date(hold.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  {hold.released_at === null && (
                    <button
                      onClick={() => handleReleaseHold(hold.id)}
                      className="text-xs px-3 py-1.5 border border-red-300 text-red-700 rounded hover:bg-red-50 whitespace-nowrap"
                    >
                      Release
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Privacy / Erasure */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-medium text-gray-900">
              Privacy Erasure Requests (GDPR / CCPA)
            </h2>
            <button
              onClick={() => setShowErasureForm(!showErasureForm)}
              className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              {showErasureForm ? "Cancel" : "New request"}
            </button>
          </div>

          {showErasureForm && (
            <div className="bg-white shadow rounded-lg p-4 mb-4 space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <input
                  type="text"
                  placeholder="Data subject (name/email)"
                  value={erasureForm.data_subject}
                  onChange={(e) => setErasureForm({ ...erasureForm, data_subject: e.target.value })}
                  className="border border-gray-300 rounded px-3 py-2 text-sm"
                />
                <input
                  type="text"
                  placeholder="Agreement ID (optional)"
                  value={erasureForm.agreement_id}
                  onChange={(e) => setErasureForm({ ...erasureForm, agreement_id: e.target.value })}
                  className="border border-gray-300 rounded px-3 py-2 text-sm"
                />
                <select
                  value={erasureForm.regulation}
                  onChange={(e) => setErasureForm({ ...erasureForm, regulation: e.target.value })}
                  className="border border-gray-300 rounded px-3 py-2 text-sm"
                >
                  <option value="gdpr">GDPR</option>
                  <option value="ccpa">CCPA</option>
                </select>
              </div>
              <button
                onClick={handleCreateErasure}
                disabled={busy || !erasureForm.data_subject}
                className="text-sm px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {busy ? "Submitting..." : "Create request"}
              </button>
            </div>
          )}

          {erasures.length === 0 ? (
            <div className="bg-white shadow rounded-lg p-6 text-sm text-gray-500">
              No erasure requests.
            </div>
          ) : (
            <div className="bg-white shadow rounded-lg divide-y divide-gray-200">
              {erasures.map((erasure) => (
                <div key={erasure.id} data-testid={`erasure-row-${erasure.data_subject}`} className="p-4 flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-900">{erasure.data_subject}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${statusBadge[erasure.status] ?? "bg-gray-100 text-gray-600"}`}>
                        {erasure.status}
                      </span>
                      <span className="text-xs uppercase text-gray-400">{erasure.regulation}</span>
                    </div>
                    {erasure.shredded_fields && erasure.shredded_fields.length > 0 && (
                      <p className="text-xs text-gray-500 mt-1">
                        Shredded: {erasure.shredded_fields.join(", ")}
                      </p>
                    )}
                    <p className="text-xs text-gray-400 mt-1">
                      Requested {new Date(
                        erasure.completed_at ?? String(new Date().toISOString())
                      ).toLocaleDateString()}
                      {erasure.completed_at && " (completed)"}
                    </p>
                  </div>
                  {/* Statuses that permit execution per the erasure lifecycle
                      ('received' → 'under_review' → 'shredded'/'completed'/'denied').
                      "pending" is not a status this backend ever sets. */}
                  {(erasure.status === "received" || erasure.status === "under_review") && (
                    <button
                      onClick={() => handleExecuteErasure(erasure.id)}
                      disabled={busy}
                      className="text-xs px-3 py-1.5 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 whitespace-nowrap"
                    >
                      Execute erasure
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
