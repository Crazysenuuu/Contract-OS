"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  type APIKeyRecord,
} from "@/lib/api";

export default function ApiKeysPage() {
  const { token } = useAuth();
  const [keys, setKeys] = useState<APIKeyRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState("");
  const [creating, setCreating] = useState(false);
  const [newKeyRaw, setNewKeyRaw] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setKeys(await listApiKeys(token));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load API keys");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    queueMicrotask(() => load());
  }, [load]);

  const handleCreate = async () => {
    if (!token || !name.trim()) return;
    setCreating(true);
    try {
      const created = await createApiKey(token, {
        name: name.trim(),
        scopes: scopes.trim() || undefined,
      });
      setNewKeyRaw(created.raw_key);
      setName("");
      setScopes("");
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create key");
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (id: string) => {
    if (!token) return;
    try {
      await revokeApiKey(token, id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke key");
    }
  };

  const copyKey = async () => {
    if (newKeyRaw) {
      await navigator.clipboard.writeText(newKeyRaw);
      setCopied(true);
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="mb-6">
        <Link href="/settings" className="text-sm text-gray-500 hover:text-gray-700">
          ← Back to Settings
        </Link>
      </div>

      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-bold text-gray-900">API Keys</h1>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-md hover:bg-brand-700"
        >
          {showCreate ? "Cancel" : "+ New API Key"}
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        Programmatic access keys (spec §7). The raw key is shown <strong>once</strong> at
        creation — store it securely; only its hash is kept server-side.
      </p>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {newKeyRaw && (
        <div className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 p-4">
          <div className="text-sm font-medium text-emerald-800 mb-2">
            API key created — copy it now, it will not be shown again:
          </div>
          <div className="flex items-center gap-3">
            <code className="flex-1 bg-white border border-emerald-300 rounded px-3 py-2 text-sm font-mono break-all">
              {newKeyRaw}
            </code>
            <button
              onClick={copyKey}
              className="px-3 py-2 text-sm bg-emerald-600 text-white rounded-md hover:bg-emerald-700 whitespace-nowrap"
            >
              {copied ? "✓ Copied" : "Copy"}
            </button>
          </div>
        </div>
      )}

      {showCreate && (
        <div className="mb-6 p-4 bg-gray-50 rounded-lg space-y-3">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Key name (e.g. CI integration)"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
          <input
            type="text"
            value={scopes}
            onChange={(e) => setScopes(e.target.value)}
            placeholder="Scopes (comma-separated permission keys, or * for all — empty = read-only)"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm font-mono"
          />
          <button
            onClick={handleCreate}
            disabled={!name.trim() || creating}
            className="px-4 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {creating ? "Creating…" : "Create API Key"}
          </button>
        </div>
      )}

      <div className="bg-white shadow rounded-lg overflow-hidden">
        {loading ? (
          <div className="py-16 text-center text-gray-500">Loading…</div>
        ) : keys.length === 0 ? (
          <div className="py-16 text-center text-sm text-gray-500">
            No API keys yet. Create one for programmatic access.
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Prefix</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Scopes</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Last Used</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {keys.map((k) => (
                <tr key={k.id}>
                  <td className="px-4 py-3 font-medium text-gray-900">{k.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-gray-500">{k.key_prefix}…</td>
                  <td className="px-4 py-3 text-xs text-gray-500 font-mono max-w-xs truncate">
                    {k.scopes ?? "(read-only)"}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "never"}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs ${
                        k.is_active
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {k.is_active ? "active" : "revoked"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {k.is_active && (
                      <button
                        onClick={() => handleRevoke(k.id)}
                        className="text-xs text-red-600 hover:text-red-800 font-medium"
                      >
                        Revoke
                      </button>
                    )}
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
