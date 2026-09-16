"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";

interface Template {
  id: string;
  name: string;
  description: string | null;
  jurisdiction: string | null;
  language: string;
  status: string;
  is_system: boolean;
  created_at: string;
  agreement_type_name: string | null;
}

const statusBadge: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600 border border-gray-200",
  active: "bg-emerald-100 text-emerald-700 border border-emerald-200",
  archived: "bg-red-50 text-red-600 border border-red-200",
};

export default function TemplatesPage() {
  const { token } = useAuth();
  const router = useRouter();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const load = useCallback(() => {
    if (!token) return;
    setLoading(true);
    fetch("/api/v1/templates?page_size=100", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      })
      .then((d) => setTemplates(d.items ?? d))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (!token) { router.push("/login"); return; }
    void Promise.resolve().then(load);
  }, [token, router, load]);

  const filtered = templates.filter(
    (t) =>
      !search ||
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      (t.description ?? "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Template Library</h1>
            <p className="text-gray-500 text-sm mt-1">
              Manage and use reusable agreement templates
            </p>
          </div>
          <Link
            id="new-template-btn"
            href="/templates/new"
            className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 transition-colors"
          >
            + New Template
          </Link>
        </div>

        {/* Search */}
        <div className="mb-6">
          <input
            id="template-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search templates…"
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
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-gray-400">
              {search ? "No templates match your search." : "No templates yet."}
            </p>
            {!search && (
              <Link
                href="/templates/new"
                className="mt-4 inline-block text-blue-600 hover:underline text-sm"
              >
                Create your first template →
              </Link>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {filtered.map((t) => (
              <div
                key={t.id}
                id={`template-card-${t.id}`}
                onClick={() => router.push(`/templates/${t.id}`)}
                className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all group"
              >
                <div className="flex items-start justify-between mb-3">
                  <span
                    className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusBadge[t.status] ?? "bg-gray-100 text-gray-600"}`}
                  >
                    {t.status}
                  </span>
                  {t.is_system && (
                    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-600 border border-blue-200">
                      System
                    </span>
                  )}
                </div>
                <h2 className="font-semibold text-gray-900 group-hover:text-blue-700 transition-colors mb-1">
                  {t.name}
                </h2>
                {t.description && (
                  <p className="text-sm text-gray-500 line-clamp-2 mb-3">
                    {t.description}
                  </p>
                )}
                <div className="flex flex-wrap gap-1.5 mt-auto">
                  {t.agreement_type_name && (
                    <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                      {t.agreement_type_name}
                    </span>
                  )}
                  {t.jurisdiction && (
                    <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                      {t.jurisdiction}
                    </span>
                  )}
                  <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full uppercase">
                    {t.language}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
