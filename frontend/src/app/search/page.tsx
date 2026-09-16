"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  createSavedSearch,
  deleteSavedSearch,
  listAgreementTypes,
  listSavedSearches,
  SavedSearch,
  searchAgreements,
  SearchFilters,
  SearchResultItem,
  updateSavedSearch,
} from "@/lib/api";

const STATUSES = [
  "draft",
  "internal_review",
  "approval",
  "sent",
  "negotiating",
  "approved",
  "signing",
  "executed",
  "active",
  "expiring",
  "expired",
  "terminated",
];

export default function SearchPage() {
  const { token } = useAuth();
  const searchParams = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;

  const [query, setQuery] = useState(searchParams?.get("q") || "");
  const [filters, setFilters] = useState<SearchFilters>({});
  const [agreementTypes, setAgreementTypes] = useState<
    Array<{ id: string; name: string }>
  >([]);

  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [total, setTotal] = useState(0);
  const [tookMs, setTookMs] = useState<number | null>(null);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);

  const [saved, setSaved] = useState<SavedSearch[]>([]);
  const [saveName, setSaveName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listAgreementTypes(token)
      .then((types) => setAgreementTypes(types))
      .catch(() => {});
    listSavedSearches(token)
      .then(setSaved)
      .catch(() => {});
  }, [token]);

  const runSearch = useCallback(
    async (q: string, f: SearchFilters) => {
      if (!token) return;
      setLoading(true);
      setError(null);
      try {
        const res = await searchAgreements(token, q, f);
        setResults(res.items);
        setTotal(res.total);
        setTookMs(res.took_ms);
        setSearched(true);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Search failed");
      } finally {
        setLoading(false);
      }
    },
    [token]
  );

  // Auto-run search when arriving with a ?q= deep link (e.g. dashboard box).
  useEffect(() => {
    if (token && query && !searched) {
      void Promise.resolve().then(() => runSearch(query, filters));
    }
  }, [token, query, searched, runSearch, filters]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    runSearch(query, filters);
  };

  const reloadSaved = useCallback(() => {
    if (!token) return;
    listSavedSearches(token)
      .then(setSaved)
      .catch(() => {});
  }, [token]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !saveName.trim()) return;
    setError(null);
    setNotice(null);
    try {
      await createSavedSearch(token, {
        name: saveName.trim(),
        query: query.trim() || undefined,
        filters,
      });
      setSaveName("");
      setNotice("Search saved");
      await reloadSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save search");
    }
  };

  const handleApply = (s: SavedSearch) => {
    setQuery(s.query ?? "");
    setFilters(s.filters ?? {});
    runSearch(s.query ?? "", s.filters ?? {});
  };

  const handleToggleFavorite = async (s: SavedSearch) => {
    if (!token) return;
    try {
      await updateSavedSearch(token, s.id, { is_favorite: !s.is_favorite });
      await reloadSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleDelete = async (s: SavedSearch) => {
    if (!token) return;
    try {
      await deleteSavedSearch(token, s.id);
      await reloadSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const setFilter = (key: keyof SearchFilters, value: string) => {
    setFilters((prev) => {
      const next = { ...prev };
      if (value) next[key] = value;
      else delete next[key];
      return next;
    });
  };

  const statusBadge = (status: string) => {
    const tone =
      status === "executed" || status === "active"
        ? "bg-emerald-100 text-emerald-800 border-emerald-200"
        : status === "expired" || status === "terminated"
          ? "bg-rose-100 text-rose-800 border-rose-200"
          : status === "draft"
            ? "bg-gray-100 text-gray-700 border-gray-200"
            : "bg-amber-100 text-amber-800 border-amber-200";
    return (
      <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border ${tone}`}>
        {status}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        <div className="mb-6">
          <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
            ← Dashboard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 mt-1">Contract Repository</h1>
          <p className="text-sm text-gray-500 mt-1">
            Full-text search across agreement titles, parties, types and governing law,
            with metadata-driven filters (spec 2.13).
          </p>
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

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Search + filters */}
          <div className="space-y-6">
            <form
              onSubmit={handleSearch}
              className="bg-white shadow rounded-lg p-6"
            >
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Search
              </label>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Title, party, clause, governing law…"
                className="w-full rounded-md border-gray-300 shadow-sm text-sm"
              />

              <div className="mt-4 space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Status</label>
                  <select
                    value={filters.status ?? ""}
                    onChange={(e) => setFilter("status", e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  >
                    <option value="">Any status</option>
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Agreement type</label>
                  <select
                    value={filters.agreement_type_id ?? ""}
                    onChange={(e) => setFilter("agreement_type_id", e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  >
                    <option value="">Any type</option>
                    {agreementTypes.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Party</label>
                  <input
                    value={filters.party ?? ""}
                    onChange={(e) => setFilter("party", e.target.value)}
                    placeholder="Counterparty name"
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Effective from</label>
                    <input
                      type="date"
                      value={filters.effective_from ?? ""}
                      onChange={(e) => setFilter("effective_from", e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Effective to</label>
                    <input
                      type="date"
                      value={filters.effective_to ?? ""}
                      onChange={(e) => setFilter("effective_to", e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Expiry from</label>
                    <input
                      type="date"
                      value={filters.expiry_from ?? ""}
                      onChange={(e) => setFilter("expiry_from", e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Expiry to</label>
                    <input
                      type="date"
                      value={filters.expiry_to ?? ""}
                      onChange={(e) => setFilter("expiry_to", e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="mt-4 w-full px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
              >
                {loading ? "Searching…" : "🔍 Search"}
              </button>
            </form>

            {/* Save search */}
            <form onSubmit={handleSave} className="bg-white shadow rounded-lg p-6">
              <h2 className="text-sm font-medium text-gray-900 mb-3">Save this search</h2>
              <div className="flex gap-2">
                <input
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  placeholder="e.g. Executed NDAs expiring soon"
                  className="flex-1 rounded-md border-gray-300 shadow-sm text-sm"
                />
                <button
                  type="submit"
                  disabled={!saveName.trim()}
                  className="px-4 py-2 text-sm font-medium text-white bg-gray-800 rounded-md hover:bg-gray-900 disabled:opacity-50"
                >
                  Save
                </button>
              </div>
            </form>

            {/* Saved searches */}
            <div className="bg-white shadow rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-sm font-medium text-gray-900">Saved searches ({saved.length})</h2>
              </div>
              {saved.length === 0 ? (
                <p className="px-6 py-8 text-sm text-gray-500 text-center">
                  No saved searches yet.
                </p>
              ) : (
                <ul className="divide-y divide-gray-200">
                  {saved.map((s) => (
                    <li key={s.id} className="px-6 py-3 flex items-center gap-3">
                      <button
                        onClick={() => handleToggleFavorite(s)}
                        className={`text-sm ${s.is_favorite ? "text-amber-500" : "text-gray-300 hover:text-gray-400"}`}
                        title={s.is_favorite ? "Unfavorite" : "Favorite"}
                      >
                        ★
                      </button>
                      <button
                        onClick={() => handleApply(s)}
                        className="flex-1 text-left"
                        title={`${s.query ?? ""} ${s.filters?.status ? `(${s.filters.status})` : ""}`}
                      >
                        <div className="text-sm font-medium text-gray-900">{s.name}</div>
                        <div className="text-[11px] text-gray-400 truncate">
                          {s.query || "all agreements"}
                          {s.filters?.status ? ` · status: ${s.filters.status}` : ""}
                        </div>
                      </button>
                      <button
                        onClick={() => handleDelete(s)}
                        className="text-xs text-rose-500 hover:text-rose-700"
                      >
                        Delete
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* Results */}
          <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
              <h2 className="text-lg font-medium text-gray-900">Results</h2>
              {searched && (
                <span className="text-xs text-gray-400">
                  {total} {total === 1 ? "agreement" : "agreements"}
                  {tookMs !== null ? ` · ${tookMs} ms` : ""}
                </span>
              )}
            </div>
            {!searched ? (
              <p className="px-6 py-12 text-sm text-gray-500 text-center">
                Enter a query and apply filters to search the repository.
              </p>
            ) : results.length === 0 ? (
              <p className="px-6 py-12 text-sm text-gray-500 text-center">
                No matching agreements.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Agreement</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Type</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Parties</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Effective</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Expiry</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {results.map((r) => (
                      <tr key={r.id} className="hover:bg-gray-50">
                        <td className="px-6 py-3">
                          <Link
                            href={`/agreements/${r.id}`}
                            className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
                          >
                            {r.title}
                          </Link>
                          {r.governing_law && (
                            <div className="text-[11px] text-gray-400 mt-0.5">
                              {r.governing_law}
                            </div>
                          )}
                        </td>
                        <td className="px-6 py-3 text-xs text-gray-500">
                          {r.agreement_type_name ?? "—"}
                        </td>
                        <td className="px-6 py-3 text-xs text-gray-600">
                          {r.party_names.length > 0 ? r.party_names.join(", ") : "—"}
                        </td>
                        <td className="px-6 py-3">{statusBadge(r.status)}</td>
                        <td className="px-6 py-3 text-xs text-gray-500">
                          {r.effective_date ?? "—"}
                        </td>
                        <td className="px-6 py-3 text-xs text-gray-500">
                          {r.expiry_date ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}