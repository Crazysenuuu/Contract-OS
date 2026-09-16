"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

interface TemplateVersion {
  id: string;
  version_number: number;
  status: string;
  created_at: string;
  locked_at: string | null;
}

interface Template {
  id: string;
  name: string;
  description: string | null;
  jurisdiction: string | null;
  language: string;
  status: string;
  is_system: boolean;
  created_at: string;
  updated_at: string;
  agreement_type_name: string | null;
  versions: TemplateVersion[];
}

export default function TemplateDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token } = useAuth();
  const router = useRouter();
  const [template, setTemplate] = useState<Template | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) { router.push("/login"); return; }
    if (!id) return;
    fetch(`/api/v1/templates/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      })
      .then(setTemplate)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [token, id, router]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error || !template) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-4">
        <p className="text-red-600">{error ?? "Template not found."}</p>
        <button onClick={() => router.push("/templates")} className="text-blue-600 hover:underline text-sm">
          ← Back to templates
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8">
        {/* Breadcrumb */}
        <nav className="text-sm text-gray-500 mb-6">
          <button onClick={() => router.push("/templates")} className="hover:text-blue-600 hover:underline">
            Templates
          </button>
          {" / "}
          <span className="text-gray-800 font-medium">{template.name}</span>
        </nav>

        {/* Title block */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-xl font-bold text-gray-900 mb-1">{template.name}</h1>
              {template.description && (
                <p className="text-gray-500 text-sm">{template.description}</p>
              )}
            </div>
            <div className="flex flex-col items-end gap-2 shrink-0">
              <span
                className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                  template.status === "active"
                    ? "bg-emerald-100 text-emerald-700 border border-emerald-200"
                    : "bg-gray-100 text-gray-600 border border-gray-200"
                }`}
              >
                {template.status}
              </span>
              {template.is_system && (
                <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-600 border border-blue-200">
                  System
                </span>
              )}
            </div>
          </div>
          <dl className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <dt className="text-xs text-gray-400 uppercase tracking-wide">Type</dt>
              <dd className="mt-0.5 text-sm text-gray-700">{template.agreement_type_name ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-400 uppercase tracking-wide">Jurisdiction</dt>
              <dd className="mt-0.5 text-sm text-gray-700">{template.jurisdiction ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-400 uppercase tracking-wide">Language</dt>
              <dd className="mt-0.5 text-sm uppercase text-gray-700">{template.language}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-400 uppercase tracking-wide">Last updated</dt>
              <dd className="mt-0.5 text-sm text-gray-700">
                {new Date(template.updated_at).toLocaleDateString()}
              </dd>
            </div>
          </dl>
        </div>

        {/* Versions */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-base font-semibold text-gray-900">Version History</h2>
            <button
              id="add-version-btn"
              onClick={() => router.push(`/templates/${id}/versions/new`)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium text-blue-600 border border-blue-200 hover:bg-blue-50 transition-colors"
            >
              + Add version
            </button>
          </div>
          {template.versions.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8">No versions yet.</p>
          ) : (
            <div className="divide-y divide-gray-100">
              {[...template.versions]
                .sort((a, b) => b.version_number - a.version_number)
                .map((v) => (
                  <div key={v.id} className="py-3 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-mono font-semibold text-gray-800">
                        v{v.version_number}
                      </span>
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          v.status === "approved"
                            ? "bg-emerald-100 text-emerald-700 border border-emerald-200"
                            : v.status === "draft"
                            ? "bg-gray-100 text-gray-600 border border-gray-200"
                            : "bg-amber-100 text-amber-700 border border-amber-200"
                        }`}
                      >
                        {v.status}
                      </span>
                    </div>
                    <div className="text-xs text-gray-400">
                      {new Date(v.created_at).toLocaleDateString()}
                      {v.locked_at && " · locked"}
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
