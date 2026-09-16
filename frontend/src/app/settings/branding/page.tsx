"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  getTenant,
  createTenant,
  getBranding,
  updateBranding,
  getTenantLimits,
} from "@/lib/api";

export default function BrandingPage() {
  const router = useRouter();
  const { token } = useAuth();
  type TenantInfo = Awaited<ReturnType<typeof getTenant>>;
  type LimitsInfo = Awaited<ReturnType<typeof getTenantLimits>>;
  interface BrandingInfo {
    company_name?: string;
    tagline?: string;
    logo_url?: string;
    favicon_url?: string;
    primary_color?: string;
    background_color?: string;
    text_color?: string;
    font_family?: string;
    footer_text?: string;
    custom_css?: string;
    [key: string]: unknown;
  }
  const [tenant, setTenant] = useState<TenantInfo | null>(null);
  const [branding, setBranding] = useState<BrandingInfo | null>(null);
  const [limits, setLimits] = useState<LimitsInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [slug, setSlug] = useState("");
  const [createMode, setCreateMode] = useState(false);

  // Declared before the effect that calls it (hoisting breaks the compiler);
  // no leading setLoading(true) so the mount effect never cascades.
  const loadData = useCallback(async () => {
    if (!token) return;
    try {
      const [tenantData, brandingData, limitsData] = await Promise.all([
        getTenant(token),
        getBranding(token).catch(() => ({}) as BrandingInfo),
        getTenantLimits(token).catch(() => ({ allowed: true })),
      ]);
      setTenant(tenantData);
      setBranding(brandingData);
      setLimits(limitsData);
    } catch (err) {
      console.error("Failed to load settings:", err);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!token) {
      router.push("/login");
      return;
    }
    queueMicrotask(() => loadData());
  }, [token, loadData, router]);

  const handleCreateTenant = async () => {
    if (!token || !slug.trim()) return;
    setSaving(true);
    try {
      await createTenant(token, slug);
      alert("Tenant created successfully!");
      setCreateMode(false);
      loadData();
    } catch (err) {
      alert(`Failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveBranding = async () => {
    if (!token) return;
    setSaving(true);
    try {
      await updateBranding(token, branding);
      alert("Branding saved!");
    } catch (err) {
      alert(`Failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setSaving(false);
    }
  };

  const updateBrandingField = (field: string, value: string) => {
    setBranding((prev) => ({ ...prev, [field]: value }));
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="text-center py-12">Loading...</div>
      </div>
    );
  }

  // No tenant yet - show create form
  if (!tenant?.exists && !createMode) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Branding & Tenant Settings</h1>
          <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
            ← Back to Dashboard
          </button>
        </div>

        <div className="bg-white rounded-lg border p-8 text-center">
          <div className="text-4xl mb-4">🏢</div>
          <h2 className="text-xl font-semibold mb-2">Set Up Your Tenant</h2>
          <p className="text-gray-500 mb-6">
            Create a tenant to enable white-label branding and multi-tenant features.
          </p>
          <button
            onClick={() => setCreateMode(true)}
            className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700"
          >
            Create Tenant
          </button>
        </div>
      </div>
    );
  }

  if (createMode) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Create Tenant</h1>
          <button onClick={() => setCreateMode(false)} className="text-blue-600 hover:underline">
            ← Cancel
          </button>
        </div>

        <div className="bg-white rounded-lg border p-6">
          <div className="mb-4">
            <label className="block text-sm font-medium mb-2">Tenant Slug (subdomain)</label>
            <div className="flex items-center">
              <input
                type="text"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="your-company"
                className="border rounded-l px-3 py-2 flex-1"
              />
              <span className="bg-gray-100 border border-l-0 rounded-r px-3 py-2 text-gray-500">
                .contractos.lk
              </span>
            </div>
          </div>

          <button
            onClick={handleCreateTenant}
            disabled={saving || !slug.trim()}
            className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? "Creating..." : "Create Tenant"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Branding & Tenant Settings</h1>
        <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
          ← Back to Dashboard
        </button>
      </div>

      {/* Tenant Info */}
      <div className="bg-white rounded-lg border p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4">Tenant Information</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <div className="text-sm text-gray-500">Slug</div>
            <div className="font-medium">{tenant?.slug}</div>
          </div>
          <div>
            <div className="text-sm text-gray-500">Plan</div>
            <div className="font-medium capitalize">{tenant?.plan}</div>
          </div>
          <div>
            <div className="text-sm text-gray-500">Users</div>
            <div className="font-medium">{limits?.usage?.users}</div>
          </div>
          <div>
            <div className="text-sm text-gray-500">Storage</div>
            <div className="font-medium">{limits?.usage?.storage_mb}</div>
          </div>
        </div>

        {limits && !limits.allowed && (
          <div className="mt-4 bg-red-50 border border-red-200 rounded p-3">
            <p className="text-sm text-red-700">
              ⚠️ Limit reached: {limits.issues?.join(", ")}
            </p>
          </div>
        )}
      </div>

      {/* Branding Editor */}
      <div className="bg-white rounded-lg border p-6 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Brand Customization</h2>
          <button
            onClick={handleSaveBranding}
            disabled={saving}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? "Saving..." : "💾 Save Changes"}
          </button>
        </div>

        <div className="space-y-6">
          {/* Company Info */}
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">Company Information</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm mb-1">Company Name</label>
                <input
                  type="text"
                  value={branding?.company_name || ""}
                  onChange={(e) => updateBrandingField("company_name", e.target.value)}
                  className="w-full border rounded px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-sm mb-1">Tagline</label>
                <input
                  type="text"
                  value={branding?.tagline || ""}
                  onChange={(e) => updateBrandingField("tagline", e.target.value)}
                  className="w-full border rounded px-3 py-2"
                />
              </div>
            </div>
          </div>

          {/* Logo */}
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">Logo</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm mb-1">Logo URL</label>
                <input
                  type="url"
                  value={branding?.logo_url || ""}
                  onChange={(e) => updateBrandingField("logo_url", e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  placeholder="https://..."
                />
              </div>
              <div>
                <label className="block text-sm mb-1">Favicon URL</label>
                <input
                  type="url"
                  value={branding?.favicon_url || ""}
                  onChange={(e) => updateBrandingField("favicon_url", e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  placeholder="https://..."
                />
              </div>
            </div>
          </div>

          {/* Colors */}
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">Colors</h3>
            <div className="grid grid-cols-3 md:grid-cols-6 gap-4">
              {[
                { field: "primary_color", label: "Primary" },
                { field: "secondary_color", label: "Secondary" },
                { field: "accent_color", label: "Accent" },
                { field: "background_color", label: "Background" },
                { field: "text_color", label: "Text" },
                { field: "error_color", label: "Error" },
              ].map(({ field, label }) => (
                <div key={field}>
                  <label className="block text-sm mb-1">{label}</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={(branding?.[field] as string | undefined) || "#000000"}
                      onChange={(e) => updateBrandingField(field, e.target.value)}
                      className="w-8 h-8 rounded cursor-pointer"
                    />
                    <input
                      type="text"
                      value={(branding?.[field] as string | undefined) || ""}
                      onChange={(e) => updateBrandingField(field, e.target.value)}
                      className="flex-1 border rounded px-2 py-1 text-sm font-mono"
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Typography */}
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">Typography</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm mb-1">Font Family</label>
                <input
                  type="text"
                  value={branding?.font_family || ""}
                  onChange={(e) => updateBrandingField("font_family", e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  placeholder="Inter, system-ui, sans-serif"
                />
              </div>
              <div>
                <label className="block text-sm mb-1">Footer Text</label>
                <input
                  type="text"
                  value={branding?.footer_text || ""}
                  onChange={(e) => updateBrandingField("footer_text", e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  placeholder="© 2024 Your Company"
                />
              </div>
            </div>
          </div>

          {/* Custom CSS */}
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">Custom CSS</h3>
            <textarea
              value={branding?.custom_css || ""}
              onChange={(e) => updateBrandingField("custom_css", e.target.value)}
              className="w-full h-32 border rounded px-3 py-2 font-mono text-sm"
              placeholder=":root { --primary: #0070f3; }"
            />
          </div>
        </div>
      </div>

      {/* Preview */}
      <div className="bg-white rounded-lg border p-6">
        <h2 className="text-lg font-semibold mb-4">Preview</h2>
        <div
          className="border rounded-lg p-6"
          style={{
            backgroundColor: branding?.background_color || "#ffffff",
            color: branding?.text_color || "#1a1a2e",
            fontFamily: branding?.font_family || "Inter, system-ui, sans-serif",
          }}
        >
          <div className="flex items-center gap-4 mb-4">
            {branding?.logo_url && (
              // eslint-disable-next-line @next/next/no-img-element -- user-supplied tenant logo URL, arbitrary origin
              <img src={branding.logo_url} alt="Logo" className="h-10" />
            )}
            <div>
              <h3 className="text-xl font-bold" style={{ color: branding?.primary_color }}>
                {branding?.company_name || "Your Company"}
              </h3>
              {branding?.tagline && (
                <p className="text-sm opacity-70">{branding.tagline}</p>
              )}
            </div>
          </div>

          <button
            className="px-4 py-2 rounded text-white text-sm"
            style={{ backgroundColor: branding?.primary_color || "#0070f3" }}
          >
            Primary Button
          </button>

          <div className="mt-4 text-sm opacity-60">
            {branding?.footer_text || "© 2024 Your Company. All rights reserved."}
          </div>
        </div>
      </div>
    </div>
  );
}
