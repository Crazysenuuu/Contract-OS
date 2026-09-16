"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  listLegalEntities,
  listSignatories,
  addSignatory,
  getEntitySigningReport,
} from "@/lib/api";

interface LegalEntity {
  id: string;
  legal_name: string;
  country: string;
}

interface Signatory {
  id: string;
  name: string;
  title: string | null;
  authority_type: string;
  authority_scope: string;
  maximum_value: number | null;
  currency: string | null;
  is_active: boolean;
}

interface SigningReport {
  total_signatories: number;
  active_signatories: number;
  inactive_signatories: number;
  authority_distribution: Record<string, { count: number; total_capacity: number }>;
  unlimited_authority: number;
}

export default function SignatureAuthorityPage() {
  const router = useRouter();
  const { token } = useAuth();
  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [selectedEntity, setSelectedEntity] = useState<string>("");
  const [signatories, setSignatories] = useState<Signatory[]>([]);
  const [report, setReport] = useState<SigningReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newSignatory, setNewSignatory] = useState({
    name: "",
    title: "",
    email: "",
    authority_type: "manager",
    authority_scope: "limited",
    maximum_value: "",
    currency: "LKR",
  });

  // Declared before the effects that call them (hoisting breaks the
  // compiler); no leading setLoading(true) so mount effects never cascade.
  const loadEntities = useCallback(async () => {
    if (!token) return;
    try {
      const data = await listLegalEntities(token);
      setEntities(data);
      if (data.length > 0) {
        setSelectedEntity(data[0].id);
      }
    } catch (err) {
      console.error("Failed to load entities:", err);
    }
  }, [token]);

  const loadEntityData = useCallback(async () => {
    if (!token || !selectedEntity) return;
    try {
      const [signatoriesData, reportData] = await Promise.all([
        listSignatories(token, selectedEntity),
        getEntitySigningReport(token, selectedEntity),
      ]);
      setSignatories(signatoriesData);
      setReport(reportData);
    } catch (err) {
      console.error("Failed to load entity data:", err);
    } finally {
      setLoading(false);
    }
  }, [token, selectedEntity]);

  useEffect(() => {
    if (!token) {
      router.push("/login");
      return;
    }
    queueMicrotask(() => loadEntities());
  }, [token, loadEntities, router]);

  useEffect(() => {
    if (selectedEntity && token) {
      queueMicrotask(() => loadEntityData());
    }
  }, [selectedEntity, token, loadEntityData]);

  const handleAddSignatory = async () => {
    if (!token || !selectedEntity) return;
    try {
      await addSignatory(token, {
        legal_entity_id: selectedEntity,
        name: newSignatory.name,
        title: newSignatory.title || undefined,
        email: newSignatory.email || undefined,
        authority_type: newSignatory.authority_type,
        authority_scope: newSignatory.authority_scope,
        maximum_value: newSignatory.maximum_value ? parseFloat(newSignatory.maximum_value) : undefined,
        currency: newSignatory.currency,
      });
      setShowAddForm(false);
      setNewSignatory({
        name: "",
        title: "",
        email: "",
        authority_type: "manager",
        authority_scope: "limited",
        maximum_value: "",
        currency: "LKR",
      });
      loadEntityData();
    } catch (err) {
      alert(`Failed: ${err instanceof Error ? err.message : "unknown error"}`);
    }
  };

  const getAuthorityColor = (type: string) => {
    switch (type) {
      case "ceo": return "text-purple-700 bg-purple-100";
      case "cfo": return "text-blue-700 bg-blue-100";
      case "director": return "text-green-700 bg-green-100";
      case "manager": return "text-orange-700 bg-orange-100";
      default: return "text-gray-700 bg-gray-100";
    }
  };

  const formatCurrency = (value: number | null, currency: string | null) => {
    if (value === null) return "Unlimited";
    const curr = currency || "LKR";
    return `${curr} ${value.toLocaleString()}`;
  };

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">🔐 Signature Authority</h1>
          <p className="text-gray-500 mt-1">Manage authorized signatories and signing authority</p>
        </div>
        <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
          ← Back
        </button>
      </div>

      {/* Entity Selector */}
      <div className="bg-white rounded-lg border p-4 mb-6">
        <label className="block text-sm font-medium mb-2">Legal Entity</label>
        <select
          value={selectedEntity}
          onChange={(e) => setSelectedEntity(e.target.value)}
          className="w-full border rounded px-3 py-2"
        >
          {entities.map((e) => (
            <option key={e.id} value={e.id}>{e.legal_name} ({e.country})</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading...</div>
      ) : (
        <>
          {/* Report Cards */}
          {report && (
            <div className="grid grid-cols-4 gap-4 mb-6">
              <div className="bg-white rounded-lg border p-4">
                <div className="text-2xl font-bold">{report.total_signatories}</div>
                <div className="text-sm text-gray-500">Total Signatories</div>
              </div>
              <div className="bg-white rounded-lg border p-4">
                <div className="text-2xl font-bold text-green-600">{report.active_signatories}</div>
                <div className="text-sm text-gray-500">Active</div>
              </div>
              <div className="bg-white rounded-lg border p-4">
                <div className="text-2xl font-bold text-purple-600">{report.unlimited_authority}</div>
                <div className="text-sm text-gray-500">Unlimited Authority</div>
              </div>
              <div className="bg-white rounded-lg border p-4">
                <div className="text-2xl font-bold text-gray-600">{report.inactive_signatories}</div>
                <div className="text-sm text-gray-500">Inactive</div>
              </div>
            </div>
          )}

          {/* Authority Distribution */}
          {report && Object.keys(report.authority_distribution).length > 0 && (
            <div className="bg-white rounded-lg border p-4 mb-6">
              <h3 className="font-medium mb-3">Authority Distribution</h3>
              <div className="grid grid-cols-4 gap-4">
                {Object.entries(report.authority_distribution).map(([type, data]) => (
                  <div key={type} className="text-center">
                    <span className={`px-3 py-1 rounded text-sm ${getAuthorityColor(type)}`}>
                      {type.toUpperCase()}
                    </span>
                    <div className="mt-2 text-lg font-semibold">{data.count}</div>
                    <div className="text-xs text-gray-500">
                      Capacity: {data.total_capacity > 0 ? `${(data.total_capacity / 1000000).toFixed(0)}M` : "Unlimited"}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Signatories List */}
          <div className="bg-white rounded-lg border">
            <div className="p-4 border-b flex items-center justify-between">
              <h2 className="font-semibold">Authorized Signatories ({signatories.length})</h2>
              <button
                onClick={() => setShowAddForm(true)}
                className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700"
              >
                + Add Signatory
              </button>
            </div>

            {signatories.length === 0 ? (
              <div className="p-8 text-center text-gray-500">
                No signatories configured. Add one to get started.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-gray-50">
                      <th className="text-left py-3 px-4">Name</th>
                      <th className="text-left py-3 px-4">Title</th>
                      <th className="text-left py-3 px-4">Authority Type</th>
                      <th className="text-left py-3 px-4">Scope</th>
                      <th className="text-left py-3 px-4">Maximum Value</th>
                      <th className="text-left py-3 px-4">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {signatories.map((s) => (
                      <tr key={s.id} className="border-b hover:bg-gray-50">
                        <td className="py-3 px-4 font-medium">{s.name}</td>
                        <td className="py-3 px-4 text-gray-600">{s.title || "—"}</td>
                        <td className="py-3 px-4">
                          <span className={`px-2 py-1 rounded text-xs ${getAuthorityColor(s.authority_type)}`}>
                            {s.authority_type.toUpperCase()}
                          </span>
                        </td>
                        <td className="py-3 px-4">
                          <span className={`px-2 py-1 rounded text-xs ${
                            s.authority_scope === "unlimited" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-700"
                          }`}>
                            {s.authority_scope}
                          </span>
                        </td>
                        <td className="py-3 px-4">{formatCurrency(s.maximum_value, s.currency)}</td>
                        <td className="py-3 px-4">
                          <span className={`px-2 py-1 rounded text-xs ${
                            s.is_active ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
                          }`}>
                            {s.is_active ? "Active" : "Inactive"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Add Signatory Modal */}
          {showAddForm && (
            <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg p-6 w-full max-w-md">
                <h3 className="text-lg font-semibold mb-4">Add Authorized Signatory</h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium mb-1">Name *</label>
                    <input
                      type="text"
                      value={newSignatory.name}
                      onChange={(e) => setNewSignatory({ ...newSignatory, name: e.target.value })}
                      className="w-full border rounded px-3 py-2"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-1">Title</label>
                    <input
                      type="text"
                      value={newSignatory.title}
                      onChange={(e) => setNewSignatory({ ...newSignatory, title: e.target.value })}
                      className="w-full border rounded px-3 py-2"
                      placeholder="e.g., CFO, Director"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-1">Email</label>
                    <input
                      type="email"
                      value={newSignatory.email}
                      onChange={(e) => setNewSignatory({ ...newSignatory, email: e.target.value })}
                      className="w-full border rounded px-3 py-2"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium mb-1">Authority Type</label>
                      <select
                        value={newSignatory.authority_type}
                        onChange={(e) => setNewSignatory({ ...newSignatory, authority_type: e.target.value })}
                        className="w-full border rounded px-3 py-2"
                      >
                        <option value="ceo">CEO</option>
                        <option value="cfo">CFO</option>
                        <option value="director">Director</option>
                        <option value="manager">Manager</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-sm font-medium mb-1">Scope</label>
                      <select
                        value={newSignatory.authority_scope}
                        onChange={(e) => setNewSignatory({ ...newSignatory, authority_scope: e.target.value })}
                        className="w-full border rounded px-3 py-2"
                      >
                        <option value="limited">Limited</option>
                        <option value="unlimited">Unlimited</option>
                      </select>
                    </div>
                  </div>
                  {newSignatory.authority_scope === "limited" && (
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-sm font-medium mb-1">Maximum Value</label>
                        <input
                          type="number"
                          value={newSignatory.maximum_value}
                          onChange={(e) => setNewSignatory({ ...newSignatory, maximum_value: e.target.value })}
                          className="w-full border rounded px-3 py-2"
                          placeholder="e.g., 5000000"
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-medium mb-1">Currency</label>
                        <select
                          value={newSignatory.currency}
                          onChange={(e) => setNewSignatory({ ...newSignatory, currency: e.target.value })}
                          className="w-full border rounded px-3 py-2"
                        >
                          <option value="LKR">LKR</option>
                          <option value="USD">USD</option>
                          <option value="EUR">EUR</option>
                          <option value="GBP">GBP</option>
                        </select>
                      </div>
                    </div>
                  )}
                </div>
                <div className="flex justify-end gap-3 mt-6">
                  <button
                    onClick={() => setShowAddForm(false)}
                    className="px-4 py-2 border rounded text-gray-600 hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleAddSignatory}
                    disabled={!newSignatory.name}
                    className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                  >
                    Add Signatory
                  </button>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
