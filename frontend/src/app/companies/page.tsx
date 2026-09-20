"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useRequireAuth } from "@/hooks/useRequireAuth";

interface ExternalParty {
  id: string;
  legal_name: string;
  trade_name: string | null;
  country: string | null;
  email: string | null;
  phone: string | null;
  type: string;
  created_at: string;
}

interface PagedResult<T> { items: T[]; total: number; }

export default function CompaniesPage() {
  const { token } = useAuth();
  useRequireAuth();
  const router = useRouter();
  const [items, setItems] = useState<ExternalParty[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 20;

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => { setDebouncedSearch(search); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(() => {
    if (!token) return;
    // No leading setLoading(true): avoids a synchronous setState cascade
    // from the mount effect; the spinner shows via the initial state.
    const qs = new URLSearchParams({
      page: String(page),
      page_size: String(PAGE_SIZE),
      ...(debouncedSearch ? { search: debouncedSearch } : {}),
    });
    fetch(`/api/v1/external-parties?${qs}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<PagedResult<ExternalParty>>;
      })
      .then((d) => { setItems(d.items); setTotal(d.total); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [token, page, debouncedSearch]);

  useEffect(() => {
    if (!token) return;
    load();
  }, [token, load]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Companies & Counterparties</h1>
            <p className="text-gray-500 text-sm mt-1">
              External parties involved in your agreements
            </p>
          </div>
          <span className="text-sm text-gray-500">{total} total</span>
        </div>

        {/* Search */}
        <div className="mb-6">
          <input
            id="company-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, email, or country…"
            className="w-full max-w-sm rounded-lg border border-gray-200 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-20">
            <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-gray-400">No counterparties found.</p>
          </div>
        ) : (
          <>
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="text-left px-5 py-3 font-semibold text-gray-600">Name</th>
                    <th className="text-left px-5 py-3 font-semibold text-gray-600">Type</th>
                    <th className="text-left px-5 py-3 font-semibold text-gray-600">Country</th>
                    <th className="text-left px-5 py-3 font-semibold text-gray-600">Email</th>
                    <th className="text-left px-5 py-3 font-semibold text-gray-600">Since</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {items.map((c) => (
                    <tr
                      key={c.id}
                      id={`company-row-${c.id}`}
                      className="hover:bg-gray-50 transition-colors cursor-pointer"
                      onClick={() => router.push(`/companies/${c.id}`)}
                    >
                      <td className="px-5 py-4">
                        <div className="font-medium text-gray-900">{c.legal_name}</div>
                        {c.trade_name && c.trade_name !== c.legal_name && (
                          <div className="text-xs text-gray-400">{c.trade_name}</div>
                        )}
                      </td>
                      <td className="px-5 py-4">
                        <span className="capitalize text-gray-600 text-xs bg-gray-100 px-2 py-0.5 rounded-full">
                          {c.type}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-gray-600">{c.country ?? "—"}</td>
                      <td className="px-5 py-4 text-gray-600">{c.email ?? "—"}</td>
                      <td className="px-5 py-4 text-gray-400 text-xs">
                        {new Date(c.created_at).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between mt-4">
                <button
                  id="prev-page-btn"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium text-gray-600 border border-gray-200 hover:bg-gray-50 disabled:opacity-40 transition-colors"
                >
                  ← Previous
                </button>
                <span className="text-sm text-gray-500">
                  Page {page} of {totalPages}
                </span>
                <button
                  id="next-page-btn"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium text-gray-600 border border-gray-200 hover:bg-gray-50 disabled:opacity-40 transition-colors"
                >
                  Next →
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
