"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useRequireAuth } from "@/hooks/useRequireAuth";
import {
  getMyOrganization,
  getTenant,
  getTenantLimits,
  getTenantThemes,
} from "@/lib/api";

interface Organization {
  id: string;
  name: string;
  slug: string;
  country: string;
  timezone?: string;
  status?: string;
}

interface TenantInfo {
  exists: boolean;
  slug?: string;
  plan?: string;
  features?: Record<string, boolean>;
  limits?: Record<string, number>;
  usage?: Record<string, number>;
}

interface TenantLimits {
  allowed: boolean;
  usage?: Record<string, string>;
  issues?: string[];
}

interface Theme {
  id: string;
  name: string;
  description: string | null;
  colors: Record<string, string>;
  is_default: boolean;
  supports_dark_mode: boolean;
}

export default function OrganizationSettingsPage() {
  const { token } = useAuth();
  useRequireAuth();
  const router = useRouter();
  const [org, setOrg] = useState<Organization | null>(null);
  const [tenant, setTenant] = useState<TenantInfo | null>(null);
  const [limits, setLimits] = useState<TenantLimits | null>(null);
  const [themes, setThemes] = useState<Theme[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const [o, t, l, th] = await Promise.all([
        getMyOrganization(token).catch(() => null),
        getTenant(token).catch(() => null),
        getTenantLimits(token).catch(() => null),
        getTenantThemes(token).catch(() => []),
      ]);
      setOrg(o);
      setTenant(t);
      setLimits(l);
      setThemes(th);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load organization");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    queueMicrotask(() => load());
  }, [token, load]);

  const usagePercent = (used?: number, max?: number) => {
    if (!used || !max || max === 0) return null;
    return Math.min(100, Math.round((used / max) * 100));
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  return (
    <div className="max-w-5xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
      <h1 className="text-2xl font-semibold text-gray-900 mb-1">
        Organization
      </h1>
      <p className="text-sm text-gray-500 mb-6">
        Organization profile, tenant plan, and usage
      </p>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      {!org ? (
        <div className="bg-white shadow rounded-lg p-6 text-sm text-gray-500">
          Organization profile unavailable.
        </div>
      ) : (
        <div className="space-y-6">
          {/* Organization profile */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-sm font-medium text-gray-900 mb-4">Profile</h2>
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-gray-500">Name</dt>
                <dd className="font-medium text-gray-900">{org.name}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Slug</dt>
                <dd className="font-mono text-gray-900">{org.slug}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Country</dt>
                <dd className="text-gray-900">{org.country}</dd>
              </div>
              {org.timezone && (
                <div>
                  <dt className="text-gray-500">Timezone</dt>
                  <dd className="text-gray-900">{org.timezone}</dd>
                </div>
              )}
              {org.status && (
                <div>
                  <dt className="text-gray-500">Status</dt>
                  <dd>
                    <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-800">
                      {org.status}
                    </span>
                  </dd>
                </div>
              )}
            </dl>
          </div>

          {/* Tenant plan & limits */}
          {tenant?.exists && (
            <div className="bg-white shadow rounded-lg p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-medium text-gray-900">Tenant</h2>
                <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 uppercase">
                  {tenant.plan} plan
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
                {tenant.limits &&
                  Object.entries(tenant.limits).map(([key, max]) => {
                    const usedKey =
                      key === "max_users"
                        ? "users"
                        : key === "storage_limit_mb"
                        ? "storage_mb"
                        : "agreements";
                    const used = tenant.usage?.[usedKey];
                    const pct = usagePercent(used, max);
                    return (
                      <div key={key} className="border border-gray-200 rounded-lg p-3">
                        <div className="text-xs text-gray-500 capitalize">
                          {key.replace(/^(max_|storage_limit_)/, "").replace(/_/g, " ")}
                        </div>
                        <div className="mt-1 text-lg font-semibold text-gray-900">
                          {used ?? 0}
                          <span className="text-sm text-gray-400"> / {max}</span>
                        </div>
                        {pct !== null && (
                          <div className="mt-2 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${
                                pct > 90 ? "bg-red-500" : pct > 70 ? "bg-yellow-500" : "bg-green-500"
                              }`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        )}
                      </div>
                    );
                  })}
              </div>
              {tenant.features && Object.keys(tenant.features).length > 0 && (
                <div className="mt-4 pt-4 border-t border-gray-200">
                  <div className="text-xs text-gray-500 uppercase tracking-wide mb-2">
                    Enabled features
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(tenant.features)
                      .filter(([, enabled]) => enabled)
                      .map(([name]) => (
                        <span
                          key={name}
                          className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-700"
                        >
                          {name.replace(/_/g, " ")}
                        </span>
                      ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Limit check */}
          {limits && (!limits.allowed || (limits.issues?.length ?? 0) > 0) && (
            <div className="p-3 bg-yellow-50 border border-yellow-200 rounded text-sm text-yellow-800">
              <span className="font-medium">Limit warnings: </span>
              {limits.issues?.join("; ") || "usage at capacity"}
            </div>
          )}

          {/* Themes */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-sm font-medium text-gray-900 mb-4">
              Available themes
            </h2>
            {themes.length === 0 ? (
              <p className="text-sm text-gray-500">
                No themes configured for this tenant. Set colors in Branding
                settings.
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {themes.map((theme) => (
                  <div
                    key={theme.id}
                    className="border border-gray-200 rounded-lg overflow-hidden"
                  >
                    <div className="flex h-10">
                      {Object.values(theme.colors)
                        .slice(0, 6)
                        .map((color, i) => (
                          <div key={i} className="flex-1" style={{ backgroundColor: color }} />
                        ))}
                    </div>
                    <div className="p-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-gray-900">
                          {theme.name}
                        </span>
                        {theme.is_default && (
                          <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-800">
                            default
                          </span>
                        )}
                      </div>
                      {theme.description && (
                        <p className="text-xs text-gray-500 mt-1">{theme.description}</p>
                      )}
                      {theme.supports_dark_mode && (
                        <span className="text-xs text-gray-400 mt-1 inline-block">
                          🌙 dark mode
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
