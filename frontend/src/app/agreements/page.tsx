"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { listAgreements, AgreementSummary } from "@/lib/api";

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  internal_review: "bg-blue-100 text-blue-800",
  pending_approval: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  sent: "bg-purple-100 text-purple-800",
  viewed: "bg-indigo-100 text-indigo-800",
  negotiation: "bg-yellow-100 text-yellow-800",
  negotiating: "bg-yellow-100 text-yellow-800",
  ready_for_signature: "bg-orange-100 text-orange-800",
  signing: "bg-orange-100 text-orange-800",
  partially_signed: "bg-amber-100 text-amber-800",
  executed: "bg-green-100 text-green-800",
  active: "bg-emerald-100 text-emerald-800",
  expiring: "bg-amber-100 text-amber-800",
  renewed: "bg-teal-100 text-teal-800",
  expired: "bg-gray-200 text-gray-700",
  terminated: "bg-red-100 text-red-800",
  superseded: "bg-slate-100 text-slate-700",
  cancelled: "bg-red-100 text-red-800",
};

function statusColor(status: string) {
  return (
    statusColors[status] ?? "bg-gray-100 text-gray-700"
  );
}

function formatDate(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString();
}

export default function AgreementsPage() {
  const { token } = useAuth();
  const [agreements, setAgreements] = useState<AgreementSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    listAgreements(token)
      .then(setAgreements)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load agreements")
      )
      .finally(() => setLoading(false));
  }, [token]);

  const statuses = useMemo(
    () => Array.from(new Set(agreements.map((a) => a.status))).sort(),
    [agreements]
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return agreements.filter((a) => {
      const matchesSearch =
        !q ||
        a.title.toLowerCase().includes(q) ||
        (a.agreement_number ?? "").toLowerCase().includes(q);
      const matchesStatus = statusFilter === "all" || a.status === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [agreements, search, statusFilter]);

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">All Agreements</h1>
          <p className="mt-1 text-sm text-gray-500">
            Browse, search, and filter every agreement in your organization.
          </p>
        </div>
        <Link
          href="/agreements/new"
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md hover:bg-blue-700"
        >
          + New Agreement
        </Link>
      </div>

      {error && (
        <div className="mb-6 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
          {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by title or agreement number…"
          className="w-full sm:w-72 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
        >
          <option value="all">All statuses</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <span className="text-sm text-gray-500">
          {filtered.length} of {agreements.length}
        </span>
      </div>

      {/* Table */}
      <div className="bg-white shadow rounded-lg overflow-hidden">
        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading agreements…</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12 px-4">
            <p className="text-gray-500">
              {agreements.length === 0
                ? "No agreements yet. Create your first agreement to get started."
                : "No agreements match your search or filters."}
            </p>
            {agreements.length === 0 && (
              <Link
                href="/agreements/new"
                className="inline-block mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700"
              >
                Create Agreement
              </Link>
            )}
          </div>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase tracking-wide border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-3 font-medium">Agreement</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Governing Law</th>
                <th className="px-4 py-3 font-medium">Effective</th>
                <th className="px-4 py-3 font-medium">Expires</th>
                <th className="px-4 py-3 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((a) => (
                <tr key={a.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/agreements/${a.id}`}
                      className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
                    >
                      {a.title}
                    </Link>
                    {a.agreement_number && (
                      <div className="text-xs text-gray-400">
                        {a.agreement_number}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block px-2 py-0.5 text-xs font-medium rounded-full ${statusColor(a.status)}`}
                    >
                      {a.status.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {a.governing_law ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {formatDate(a.effective_date)}
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {formatDate(a.expiry_date)}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {formatDate(a.created_at)}
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
