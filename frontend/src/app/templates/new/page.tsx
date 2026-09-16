"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

interface AgreementType { id: string; name: string; }

export default function NewTemplatePage() {
  const { token } = useAuth();
  const router = useRouter();
  const [agreementTypes, setAgreementTypes] = useState<AgreementType[]>([]);
  const [form, setForm] = useState({
    name: "",
    description: "",
    agreement_type_id: "",
    jurisdiction: "",
    language: "en",
    status: "draft",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) { router.push("/login"); return; }
    fetch("/api/v1/agreement-types?page_size=200", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.ok ? r.json() : { items: [] })
      .then((d) => setAgreementTypes(d.items ?? d))
      .catch(() => {});
  }, [token, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      const r = await fetch("/api/v1/templates", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ...form,
          agreement_type_id: form.agreement_type_id || null,
          jurisdiction: form.jurisdiction || null,
          description: form.description || null,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail ?? `${r.status}`);
      }
      const created = await r.json();
      router.push(`/templates/${created.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save template");
    } finally {
      setSubmitting(false);
    }
  };

  const field = (key: keyof typeof form) => ({
    value: form[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      setForm((prev) => ({ ...prev, [key]: e.target.value })),
  });

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-2xl mx-auto px-4 sm:px-6 py-8">
        {/* Breadcrumb */}
        <nav className="text-sm text-gray-500 mb-6">
          <button onClick={() => router.push("/templates")} className="hover:text-blue-600 hover:underline">
            Templates
          </button>
          {" / "}
          <span className="text-gray-800 font-medium">New Template</span>
        </nav>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <h1 className="text-xl font-bold text-gray-900 mb-6">Create Template</h1>

          {error && (
            <div className="mb-5 p-4 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
              {error}
            </div>
          )}

          <form id="new-template-form" onSubmit={handleSubmit} className="space-y-5">
            {/* Name */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="tmpl-name">
                Name <span className="text-red-500">*</span>
              </label>
              <input
                id="tmpl-name"
                type="text"
                required
                {...field("name")}
                placeholder="e.g. Standard NDA (Sri Lanka)"
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            {/* Description */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="tmpl-desc">
                Description
              </label>
              <textarea
                id="tmpl-desc"
                rows={3}
                {...field("description")}
                placeholder="Optional – describe this template's intended use"
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              />
            </div>

            {/* Agreement type */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="tmpl-type">
                Agreement Type
              </label>
              <select
                id="tmpl-type"
                {...field("agreement_type_id")}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
              >
                <option value="">— Select (optional) —</option>
                {agreementTypes.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* Jurisdiction */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="tmpl-jur">
                  Jurisdiction
                </label>
                <input
                  id="tmpl-jur"
                  type="text"
                  {...field("jurisdiction")}
                  placeholder="e.g. LK, US-CA"
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              {/* Language */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="tmpl-lang">
                  Language
                </label>
                <select
                  id="tmpl-lang"
                  {...field("language")}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                >
                  <option value="en">English</option>
                  <option value="si">Sinhala</option>
                  <option value="ta">Tamil</option>
                </select>
              </div>
            </div>

            {/* Status */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="tmpl-status">
                Initial Status
              </label>
              <select
                id="tmpl-status"
                {...field("status")}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
              >
                <option value="draft">Draft</option>
                <option value="active">Active</option>
              </select>
            </div>

            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={() => router.push("/templates")}
                className="flex-1 px-4 py-2 rounded-lg text-sm font-medium text-gray-700 border border-gray-200 hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
              <button
                id="submit-template-btn"
                type="submit"
                disabled={submitting}
                className="flex-1 px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 transition-colors disabled:opacity-50"
              >
                {submitting ? "Creating…" : "Create Template"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
