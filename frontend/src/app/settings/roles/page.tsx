"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  addRbacMember,
  createRbacRole,
  deleteRbacRole,
  listRbacPermissions,
  listRbacRoles,
  updateRbacRolePermissions,
  type RbacPermission,
  type RbacRole,
} from "@/lib/api";

const SYSTEM_ROLES = new Set(["owner", "admin"]);

function groupPermissions(perms: RbacPermission[]) {
  const groups: Record<string, RbacPermission[]> = {};
  for (const p of perms) {
    const [prefix] = p.key.split(".");
    (groups[prefix] ??= []).push(p);
  }
  return groups;
}

export default function RolesPage() {
  const { token } = useAuth();
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [permissions, setPermissions] = useState<RbacPermission[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newRole, setNewRole] = useState({ name: "", description: "" });
  const [member, setMember] = useState({ user_id: "", role_id: "" });

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [r, p] = await Promise.all([listRbacRoles(token), listRbacPermissions(token)]);
      setRoles(r);
      setPermissions(p);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load roles");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    queueMicrotask(() => load());
  }, [load]);

  const selected = useMemo(() => roles.find((r) => r.id === selectedId) ?? null, [roles, selectedId]);

  useEffect(() => {
    if (selected) queueMicrotask(() => setDraft(new Set(selected.permissions.map((p) => p.key))));
  }, [selected]);

  const grouped = useMemo(() => groupPermissions(permissions), [permissions]);
  const dirty = useMemo(() => {
    if (!selected) return false;
    const current = new Set(selected.permissions.map((p) => p.key));
    if (current.size !== draft.size) return true;
    return Array.from(draft).some((k) => !current.has(k));
  }, [selected, draft]);

  const flash = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(null), 2500);
  };

  const toggle = (key: string) =>
    setDraft((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const savePermissions = async () => {
    if (!token || !selected) return;
    setSaving(true);
    try {
      await updateRbacRolePermissions(token, selected.id, Array.from(draft));
      await load();
      flash(`Permissions saved for ${selected.name}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save permissions");
    } finally {
      setSaving(false);
    }
  };

  const create = async () => {
    if (!token || !newRole.name.trim()) return;
    setSaving(true);
    try {
      const role = await createRbacRole(token, {
        name: newRole.name.trim().toLowerCase(),
        description: newRole.description || undefined,
        permission_keys: [],
      });
      setNewRole({ name: "", description: "" });
      await load();
      setSelectedId(role.id);
      flash(`Role “${role.name}” created — now assign its permissions`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create role");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (role: RbacRole) => {
    if (!token || !confirm(`Delete role “${role.name}”?`)) return;
    try {
      await deleteRbacRole(token, role.id);
      if (selectedId === role.id) setSelectedId(null);
      await load();
      flash("Role deleted");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete role");
    }
  };

  const addMember = async () => {
    if (!token || !member.user_id || !member.role_id) return;
    setSaving(true);
    try {
      await addRbacMember(token, member);
      setMember({ user_id: "", role_id: "" });
      flash("Member added");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add member");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="p-8 text-gray-500">Loading roles…</div>;
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <Link href="/settings" className="text-sm text-gray-500 hover:text-gray-900">
            ← Settings
          </Link>
          <h1 className="mt-1 text-2xl font-bold text-gray-900">Roles &amp; permissions</h1>
          <p className="text-sm text-gray-500">
            Who may create, send, approve, sign and terminate agreements. <span className="font-medium">owner</span> and{" "}
            <span className="font-medium">admin</span> hold every permission.
          </p>
        </div>
        {notice && (
          <div className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-700 ring-1 ring-green-200">{notice}</div>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Role list + create */}
        <div className="space-y-4">
          <div className="rounded-lg bg-white shadow">
            <ul className="divide-y divide-gray-100">
              {roles.map((r) => {
                const active = r.id === selectedId;
                const system = SYSTEM_ROLES.has(r.name);
                return (
                  <li key={r.id}>
                    <button
                      onClick={() => setSelectedId(r.id)}
                      className={`flex w-full items-center justify-between px-4 py-3 text-left transition ${
                        active ? "bg-blue-50" : "hover:bg-gray-50"
                      }`}
                    >
                      <div>
                        <div className="text-sm font-medium text-gray-900">{r.name}</div>
                        <div className="text-xs text-gray-500">
                          {system ? "All permissions" : `${r.permissions.length} permission${r.permissions.length === 1 ? "" : "s"}`}
                        </div>
                      </div>
                      {system && (
                        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-600">
                          system
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="rounded-lg bg-white p-4 shadow">
            <h2 className="mb-3 text-sm font-semibold text-gray-900">New role</h2>
            <input
              className="mb-2 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="e.g. legal_reviewer"
              value={newRole.name}
              onChange={(e) => setNewRole({ ...newRole, name: e.target.value })}
            />
            <input
              className="mb-3 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Description (optional)"
              value={newRole.description}
              onChange={(e) => setNewRole({ ...newRole, description: e.target.value })}
            />
            <button
              disabled={saving || !newRole.name.trim()}
              onClick={create}
              className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Create role
            </button>
          </div>

          <div className="rounded-lg bg-white p-4 shadow">
            <h2 className="mb-3 text-sm font-semibold text-gray-900">Add member</h2>
            <input
              className="mb-2 w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono"
              placeholder="User ID (uuid)"
              value={member.user_id}
              onChange={(e) => setMember({ ...member, user_id: e.target.value })}
            />
            <select
              className="mb-3 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              value={member.role_id}
              onChange={(e) => setMember({ ...member, role_id: e.target.value })}
            >
              <option value="">Select role…</option>
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
            <button
              disabled={saving || !member.user_id || !member.role_id}
              onClick={addMember}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Add to organisation
            </button>
          </div>
        </div>

        {/* Permission matrix */}
        <div className="lg:col-span-2">
          {!selected ? (
            <div className="flex h-full min-h-[240px] items-center justify-center rounded-lg border-2 border-dashed border-gray-200 text-sm text-gray-500">
              Select a role to edit its permissions
            </div>
          ) : (
            <div className="rounded-lg bg-white shadow">
              <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
                <div>
                  <h2 className="text-lg font-medium text-gray-900">{selected.name}</h2>
                  <p className="text-xs text-gray-500">{selected.description || "—"}</p>
                </div>
                <div className="flex items-center gap-2">
                  {!SYSTEM_ROLES.has(selected.name) && (
                    <button
                      onClick={() => remove(selected)}
                      className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50"
                    >
                      Delete
                    </button>
                  )}
                  <button
                    disabled={saving || !dirty || SYSTEM_ROLES.has(selected.name)}
                    onClick={savePermissions}
                    className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {saving ? "Saving…" : dirty ? "Save changes" : "Saved"}
                  </button>
                </div>
              </div>

              {SYSTEM_ROLES.has(selected.name) ? (
                <div className="px-6 py-8 text-sm text-gray-500">
                  System roles implicitly hold every permission and cannot be edited.
                </div>
              ) : (
                <div className="divide-y divide-gray-100">
                  {Object.entries(grouped).map(([group, perms]) => (
                    <div key={group} className="px-6 py-4">
                      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                        {group.replace(/_/g, " ")}
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {perms.map((p) => {
                          const on = draft.has(p.key);
                          return (
                            <label
                              key={p.key}
                              className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2 transition ${
                                on ? "border-blue-300 bg-blue-50" : "border-gray-200 hover:bg-gray-50"
                              }`}
                            >
                              <input type="checkbox" className="mt-0.5" checked={on} onChange={() => toggle(p.key)} />
                              <span>
                                <span className="block font-mono text-xs text-gray-900">{p.key}</span>
                                <span className="block text-xs text-gray-500">{p.description}</span>
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
