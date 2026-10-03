"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";

/**
 * Organization picker for accounts holding several active memberships.
 *
 * Renders nothing for single-organization users: the backend resolves the
 * tenant without a header in that case, so there is no choice to make and an
 * extra control would only imply one.
 */
export function OrganizationSwitcher() {
  const { organizations, currentOrganizationId, switchOrganization, organizationsLoaded } =
    useAuth();

  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!organizationsLoaded || organizations.length < 2) {
    return null;
  }

  const current = organizations.find((o) => o.organization_id === currentOrganizationId);

  const select = async (organizationId: string) => {
    if (organizationId === currentOrganizationId) {
      setOpen(false);
      return;
    }
    setPendingId(organizationId);
    setError(null);
    try {
      await switchOrganization(organizationId);
      setOpen(false);
    } catch {
      // The membership list and the selection disagree, so this org is no
      // longer reachable. Say so rather than silently doing nothing.
      setError("That organization is no longer available.");
    } finally {
      setPendingId(null);
    }
  };

  return (
    <div ref={containerRef} className="relative px-2 pb-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex w-full items-center justify-between gap-1 rounded-lg border border-gray-200 px-2 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
      >
        <span className="truncate font-medium">{current?.name ?? "Select organization"}</span>
        <span aria-hidden="true" className="text-[10px] text-gray-400">
          {open ? "▲" : "▼"}
        </span>
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute bottom-full left-2 right-2 z-20 mb-1 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-lg"
        >
          {organizations.map((org) => {
            const active = org.organization_id === currentOrganizationId;
            return (
              <button
                key={org.organization_id}
                type="button"
                role="option"
                aria-selected={active}
                disabled={pendingId !== null}
                onClick={() => select(org.organization_id)}
                className={`flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left text-xs hover:bg-gray-50 disabled:opacity-60 ${
                  active ? "font-semibold text-gray-900" : "text-gray-700"
                }`}
              >
                <span className="truncate">{org.name}</span>
                {pendingId === org.organization_id && (
                  <span className="text-[10px] text-gray-400">switching…</span>
                )}
                {active && pendingId === null && (
                  <span aria-hidden="true" className="text-[10px] text-gray-400">
                    ✓
                  </span>
                )}
              </button>
            );
          })}
          {error && (
            <p className="border-t border-gray-100 px-2.5 py-1.5 text-[11px] text-rose-600">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}