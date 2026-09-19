"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  createScimToken,
  createSsoConnection,
  listSsoConnections,
  testSsoConnection,
  type SsoConnection,
} from "@/lib/api";

type Draft = {
  protocol: "oidc" | "saml";
  name: string;
  issuer: string;
  client_id: string;
  client_secret_ref: string;
  idp_metadata_url: string;
  domains: string;
  default_role: string;
  enforce_sso: boolean;
};

const empty: Draft = {
  protocol: "oidc",
  name: "",
  issuer: "",
  client_id: "",
  client_secret_ref: "",
  idp_metadata_url: "",
  domains: "",
  default_role: "member",
  enforce_sso: false,
};

export default function SsoSettingsPage() {
  const { token } = useAuth();
  const [connections, setConnections] = useState<SsoConnection[]>([]);
  const [draft, setDraft] = useState<Draft>(empty);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [scimToken, setScimToken] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setConnections(await listSsoConnections(token));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load SSO connections");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    queueMicrotask(() => load());
  }, [load]);

  const save = async () => {
    if (!token || !draft.name.trim()) return;
    setSaving(true);
    try {
      await createSsoConnection(token, {
        protocol: draft.protocol,
        name: draft.name.trim(),
        issuer: draft.issuer || undefined,
        client_id: draft.client_id || undefined,
        client_secret_ref: draft.client_secret_ref || undefined,
        idp_metadata_url: draft.idp_metadata_url || undefined,
        domains: draft.domains.split(",").map((d) => d.trim().toLowerCase()).filter(Boolean),
        default_role: draft.default_role || undefined,
        enforce_sso: draft.enforce_sso,
      });
      setDraft(empty);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create connection");
    } finally {
      setSaving(false);
    }
  };

  const test = async (c: SsoConnection) => {
    if (!token) return;
    setTestResult((r) => ({ ...r, [c.id]: "Testing…" }));
    try {
      const res = await testSsoConnection(token, c.id);
      setTestResult((r) => ({ ...r, [c.id]: res.ok === false ? `Failed: ${res.detail ?? "unknown"}` : "Reachable ✓" }));
    } catch (e) {
      setTestResult((r) => ({ ...r, [c.id]: e instanceof Error ? e.message : "Failed" }));
    }
  };

  const mintScim = async (c?: SsoConnection) => {
    if (!token) return;
    try {
      const res = await createScimToken(token, c?.id);
      setScimToken(res.token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create SCIM token");
    }
  };

  if (loading) return <div className="p-8 text-gray-500">Loading SSO settings…</div>;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <Link href="/settings" className="text-sm text-gray-500 hover:text-gray-900">
        ← Settings
      </Link>
      <h1 className="mt-1 text-2xl font-bold text-gray-900">Single sign-on &amp; SCIM</h1>
      <p className="mb-6 text-sm text-gray-500">
        Connect your identity provider (OIDC / SAML). Users whose email domain matches are routed to SSO; SCIM tokens let
        the IdP provision and deprovision accounts.
      </p>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {scimToken && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4">
          <div className="text-sm font-medium text-amber-900">SCIM bearer token — shown once</div>
          <code className="mt-2 block break-all rounded bg-white px-3 py-2 font-mono text-xs text-gray-900 ring-1 ring-amber-200">
            {scimToken}
          </code>
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => navigator.clipboard?.writeText(scimToken)}
              className="rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700"
            >
              Copy
            </button>
            <button onClick={() => setScimToken(null)} className="rounded-md px-3 py-1.5 text-xs text-amber-900 hover:underline">
              Dismiss
            </button>
          </div>
          <p className="mt-2 text-xs text-amber-800">
            SCIM base URL: <code className="font-mono">{typeof window !== "undefined" ? window.location.origin : ""}/api/v1/scim/v2</code>
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <div className="rounded-lg bg-white shadow">
            <div className="border-b border-gray-100 px-6 py-4">
              <h2 className="text-lg font-medium text-gray-900">Connections</h2>
            </div>
            {connections.length === 0 ? (
              <div className="px-6 py-10 text-center text-sm text-gray-500">No identity providers connected yet.</div>
            ) : (
              <ul className="divide-y divide-gray-100">
                {connections.map((c) => (
                  <li key={c.id} className="px-6 py-4">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-gray-900">{c.name}</span>
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-gray-600">
                            {c.protocol}
                          </span>
                          {c.enforce_sso && (
                            <span className="rounded-full bg-purple-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-purple-700">
                              enforced
                            </span>
                          )}
                          <span
                            className={`h-2 w-2 rounded-full ${c.enabled ? "bg-green-500" : "bg-gray-300"}`}
                            title={c.enabled ? "Enabled" : "Disabled"}
                          />
                        </div>
                        <div className="mt-1 text-xs text-gray-500">
                          {c.issuer || "—"} · domains: {c.domains.length ? c.domains.join(", ") : "none"} · default role:{" "}
                          {c.default_role || "—"}
                        </div>
                        {testResult[c.id] && <div className="mt-1 text-xs text-gray-700">{testResult[c.id]}</div>}
                      </div>
                      <div className="flex shrink-0 gap-2">
                        <button
                          onClick={() => test(c)}
                          className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
                        >
                          Test
                        </button>
                        <button
                          onClick={() => mintScim(c)}
                          className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
                        >
                          SCIM token
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="lg:col-span-2">
          <div className="rounded-lg bg-white p-6 shadow">
            <h2 className="mb-4 text-lg font-medium text-gray-900">Add identity provider</h2>
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                {(["oidc", "saml"] as const).map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setDraft({ ...draft, protocol: p })}
                    className={`rounded-md border px-3 py-2 text-sm font-medium uppercase transition ${
                      draft.protocol === p ? "border-blue-500 bg-blue-50 text-blue-700" : "border-gray-300 text-gray-600 hover:bg-gray-50"
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>
              <input
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                placeholder="Display name, e.g. Okta"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
              <input
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                placeholder="Issuer URL"
                value={draft.issuer}
                onChange={(e) => setDraft({ ...draft, issuer: e.target.value })}
              />
              {draft.protocol === "oidc" ? (
                <>
                  <input
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                    placeholder="Client ID"
                    value={draft.client_id}
                    onChange={(e) => setDraft({ ...draft, client_id: e.target.value })}
                  />
                  <input
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                    placeholder="Client secret reference (vault key)"
                    value={draft.client_secret_ref}
                    onChange={(e) => setDraft({ ...draft, client_secret_ref: e.target.value })}
                  />
                </>
              ) : (
                <input
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  placeholder="IdP metadata URL"
                  value={draft.idp_metadata_url}
                  onChange={(e) => setDraft({ ...draft, idp_metadata_url: e.target.value })}
                />
              )}
              <input
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                placeholder="Email domains, comma separated"
                value={draft.domains}
                onChange={(e) => setDraft({ ...draft, domains: e.target.value })}
              />
              <input
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                placeholder="Default role for new users"
                value={draft.default_role}
                onChange={(e) => setDraft({ ...draft, default_role: e.target.value })}
              />
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={draft.enforce_sso}
                  onChange={(e) => setDraft({ ...draft, enforce_sso: e.target.checked })}
                />
                Enforce SSO for these domains (disables password login)
              </label>
              <button
                disabled={saving || !draft.name.trim()}
                onClick={save}
                className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {saving ? "Saving…" : "Create connection"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
