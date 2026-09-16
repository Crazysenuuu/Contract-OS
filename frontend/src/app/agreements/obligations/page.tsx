"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  getAgreement,
  listObligations,
  createObligation,
  updateObligationStatus,
  getObligationStats,
} from "@/lib/api";

interface Agreement {
  id: string;
  title: string;
  status: string;
}

interface Obligation {
  id: string;
  agreement_id: string;
  owner_party: string;
  description: string;
  obligation_type: string;
  amount: string | null;
  frequency: string | null;
  due_date: string | null;
  status: string;
  clause_identifier: string | null;
  created_at: string;
}

interface ObligationStats {
  total: number;
  upcoming: number;
  due: number;
  completed: number;
  overdue: number;
  due_within_30_days: number;
}

const statusColors: Record<string, string> = {
  upcoming: "bg-blue-100 text-blue-800",
  due: "bg-yellow-100 text-yellow-800",
  overdue: "bg-red-100 text-red-800",
  completed: "bg-green-100 text-green-800",
  waived: "bg-gray-100 text-gray-800",
  disputed: "bg-purple-100 text-purple-800",
};

const typeLabels: Record<string, string> = {
  payment: "Payment",
  delivery: "Delivery",
  reporting: "Reporting",
  compliance: "Compliance",
  notification: "Notification",
  maintenance: "Maintenance",
  insurance: "Insurance",
  other: "Other",
};

function ObligationsContent() {
  const searchParams = useSearchParams();
  const agreementId = searchParams.get("id");
  const { token } = useAuth();

  const [agreement, setAgreement] = useState<Agreement | null>(null);
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [stats, setStats] = useState<ObligationStats | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<string>("all");

  // New obligation form
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({
    owner_party: "",
    description: "",
    obligation_type: "payment",
    amount: "",
    frequency: "once",
    due_date: "",
    clause_identifier: "",
  });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (token && agreementId) {
      Promise.all([
        getAgreement(token, agreementId),
        listObligations(token, agreementId).catch(() => []),
        getObligationStats(token, agreementId).catch(() => null),
      ])
        .then(([agr, obls, st]) => {
          setAgreement(agr);
          setObligations(obls);
          setStats(st);
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token, agreementId]);

  const handleCreate = async () => {
    if (!token || !agreementId) return;

    setCreating(true);
    setError("");
    try {
      const newObligation = await createObligation(token, agreementId, {
        owner_party: formData.owner_party,
        description: formData.description,
        obligation_type: formData.obligation_type,
        amount: formData.amount || undefined,
        frequency: formData.frequency || undefined,
        due_date: formData.due_date || undefined,
        clause_identifier: formData.clause_identifier || undefined,
      });

      setObligations([
        ...obligations,
        {
          id: newObligation.id,
          agreement_id: agreementId,
          ...formData,
          status: "upcoming",
          created_at: new Date().toISOString(),
        } as Obligation,
      ]);

      setShowForm(false);
      setFormData({
        owner_party: "",
        description: "",
        obligation_type: "payment",
        amount: "",
        frequency: "once",
        due_date: "",
        clause_identifier: "",
      });

      // Refresh stats
      const newStats = await getObligationStats(token, agreementId);
      setStats(newStats);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create");
    } finally {
      setCreating(false);
    }
  };

  const handleStatusChange = async (obligationId: string, newStatus: string) => {
    if (!token || !agreementId) return;

    try {
      await updateObligationStatus(token, agreementId, obligationId, newStatus);
      setObligations(
        obligations.map((o) =>
          o.id === obligationId ? { ...o, status: newStatus } : o
        )
      );

      // Refresh stats
      const newStats = await getObligationStats(token, agreementId);
      setStats(newStats);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const filteredObligations =
    filter === "all"
      ? obligations
      : obligations.filter((o) => o.status === filter);

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  if (!agreement) {
    return (
      <div className="text-center py-12 text-gray-500">
        Agreement not found
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          href={`/agreements/${agreementId}`}
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          ← Back to Agreement
        </Link>
      </div>

      <div className="flex justify-between items-start mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Obligations Tracking
          </h1>
          <p className="text-sm text-gray-500 mt-1">{agreement.title}</p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700"
        >
          + Add Obligation
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4 mb-6">
          <div className="bg-white shadow rounded-lg p-4 text-center">
            <div className="text-2xl font-bold text-gray-900">
              {stats.total}
            </div>
            <div className="text-xs text-gray-500">Total</div>
          </div>
          <div className="bg-white shadow rounded-lg p-4 text-center">
            <div className="text-2xl font-bold text-blue-600">
              {stats.upcoming}
            </div>
            <div className="text-xs text-gray-500">Upcoming</div>
          </div>
          <div className="bg-white shadow rounded-lg p-4 text-center">
            <div className="text-2xl font-bold text-yellow-600">
              {stats.due}
            </div>
            <div className="text-xs text-gray-500">Due</div>
          </div>
          <div className="bg-white shadow rounded-lg p-4 text-center">
            <div className="text-2xl font-bold text-red-600">
              {stats.overdue}
            </div>
            <div className="text-xs text-gray-500">Overdue</div>
          </div>
          <div className="bg-white shadow rounded-lg p-4 text-center">
            <div className="text-2xl font-bold text-green-600">
              {stats.completed}
            </div>
            <div className="text-xs text-gray-500">Completed</div>
          </div>
          <div className="bg-white shadow rounded-lg p-4 text-center">
            <div className="text-2xl font-bold text-orange-600">
              {stats.due_within_30_days}
            </div>
            <div className="text-xs text-gray-500">Due in 30 days</div>
          </div>
        </div>
      )}

      {/* New Obligation Form */}
      {showForm && (
        <div className="bg-white shadow rounded-lg p-6 mb-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            New Obligation
          </h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-700 mb-1">
                Owner Party
              </label>
              <input
                type="text"
                value={formData.owner_party}
                onChange={(e) =>
                  setFormData({ ...formData, owner_party: e.target.value })
                }
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                placeholder="e.g., ABC Technologies"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">
                Type
              </label>
              <select
                value={formData.obligation_type}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    obligation_type: e.target.value,
                  })
                }
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                {Object.entries(typeLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="col-span-2">
              <label className="block text-sm text-gray-700 mb-1">
                Description
              </label>
              <textarea
                value={formData.description}
                onChange={(e) =>
                  setFormData({ ...formData, description: e.target.value })
                }
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                rows={2}
                placeholder="Describe the obligation..."
              />
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">
                Amount
              </label>
              <input
                type="text"
                value={formData.amount}
                onChange={(e) =>
                  setFormData({ ...formData, amount: e.target.value })
                }
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                placeholder="e.g., $10,000"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">
                Frequency
              </label>
              <select
                value={formData.frequency}
                onChange={(e) =>
                  setFormData({ ...formData, frequency: e.target.value })
                }
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                <option value="once">Once</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annually">Annually</option>
              </select>
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">
                Due Date
              </label>
              <input
                type="date"
                value={formData.due_date}
                onChange={(e) =>
                  setFormData({ ...formData, due_date: e.target.value })
                }
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">
                Clause Reference
              </label>
              <input
                type="text"
                value={formData.clause_identifier}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    clause_identifier: e.target.value,
                  })
                }
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                placeholder="e.g., section.5"
              />
            </div>
          </div>
          <div className="flex space-x-3 mt-4">
            <button
              onClick={handleCreate}
              disabled={
                creating || !formData.owner_party || !formData.description
              }
              className="px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? "Creating..." : "Create Obligation"}
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="px-4 py-2 border border-gray-300 text-sm rounded-md hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex space-x-2 mb-4">
        {["all", "upcoming", "due", "overdue", "completed"].map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1 text-sm rounded-full ${
              filter === f
                ? "bg-blue-600 text-white"
                : "bg-gray-100 text-gray-700 hover:bg-gray-200"
            }`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Obligations List */}
      <div className="bg-white shadow rounded-lg overflow-hidden">
        {filteredObligations.length === 0 ? (
          <div className="p-6 text-center text-gray-500">
            No obligations {filter !== "all" ? `with status "${filter}"` : ""}.
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Obligation
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Owner
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Type
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Due Date
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {filteredObligations.map((obligation) => (
                <tr key={obligation.id}>
                  <td className="px-6 py-4">
                    <div className="text-sm text-gray-900">
                      {obligation.description}
                    </div>
                    {obligation.clause_identifier && (
                      <div className="text-xs text-gray-500">
                        {obligation.clause_identifier}
                      </div>
                    )}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-900">
                    {obligation.owner_party}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-900">
                    {typeLabels[obligation.obligation_type] ||
                      obligation.obligation_type}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-900">
                    {obligation.due_date || "—"}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`px-2 py-1 text-xs font-medium rounded-full ${
                        statusColors[obligation.status] || statusColors.upcoming
                      }`}
                    >
                      {obligation.status}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <select
                      value={obligation.status}
                      onChange={(e) =>
                        handleStatusChange(obligation.id, e.target.value)
                      }
                      className="text-xs border border-gray-300 rounded px-2 py-1"
                    >
                      <option value="upcoming">Upcoming</option>
                      <option value="due">Due</option>
                      <option value="overdue">Overdue</option>
                      <option value="completed">Completed</option>
                      <option value="waived">Waived</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default function ObligationsPage() {
  return (
    <Suspense fallback={<div className="text-center py-12 text-gray-500">Loading...</div>}>
      <ObligationsContent />
    </Suspense>
  );
}
