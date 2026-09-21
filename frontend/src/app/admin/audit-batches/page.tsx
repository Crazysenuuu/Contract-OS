"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  adminSealAuditBatch,
  adminVerifyAuditBatch,
  AuditBatchInfo,
  AuditBatchVerifyResult,
} from "@/lib/api";

interface VerifyRow {
  batchId: string;
  result: AuditBatchVerifyResult | null;
  error: string | null;
}

const anchorBadge: Record<string, { label: string; cls: string }> = {
  externally_anchored: {
    label: "Externally anchored",
    cls: "bg-emerald-100 text-emerald-800 border-emerald-300",
  },
  internal_only: {
    label: "Internal only",
    cls: "bg-gray-100 text-gray-700 border-gray-300",
  },
  anchor_failed: {
    label: "Anchor failed",
    cls: "bg-orange-100 text-orange-800 border-orange-300",
  },
};

export default function AuditBatchesAdminPage() {
  const { token, user, isLoading } = useAuth();
  const router = useRouter();

  const [fromSequence, setFromSequence] = useState("");
  const [toSequence, setToSequence] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [sealing, setSealing] = useState(false);
  const [verifying, setVerifying] = useState<string | null>(null);

  // Verified batches live only in component state (this page lists results
  // of actions taken here; the backend is the source of truth for batches).
  const [sealed, setSealed] = useState<AuditBatchInfo[]>([]);
  const [verifications, setVerifications] = useState<Record<string, VerifyRow>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Admin gate + session-expiry notice (matches other admin pages).
  useEffect(() => {
    if (!isLoading && !token) {
      router.replace("/login");
      return;
    }
    if (!isLoading && token && user && !user.is_admin) {
      router.replace("/dashboard");
    }
  }, [isLoading, token, user, router]);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const flash = useCallback((message: string) => {
    setNotice(message);
    timer.current = setTimeout(() => setNotice(null), 5000);
  }, []);

  const handleSeal = async () => {
    if (!token) return;
    const from = parseInt(fromSequence, 10);
    const to = parseInt(toSequence, 10);
    if (!Number.isFinite(from) || !Number.isFinite(to) || from <= 0 || to < from) {
      setError("Enter a valid sequence range (from ≥ 1, to ≥ from).");
      return;
    }
    setSealing(true);
    setError(null);
    try {
      const result = await adminSealAuditBatch(token, {
        from_sequence: from,
        to_sequence: to,
        ...(tenantId.trim() ? { tenant_id: tenantId.trim() } : {}),
      });
      setSealed((prev) => [result.batch, ...prev.filter((b) => b.id !== result.batch.id)]);
      flash(
        result.batch.anchor_status === "externally_anchored"
          ? `Batch sealed and externally anchored (${result.batch.leaf_count} events).`
          : `Batch sealed (${result.batch.leaf_count} events, ${result.batch.anchor_status.replace("_", " ")}).`
      );
      setFromSequence("");
      setToSequence("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sealing failed");
    } finally {
      setSealing(false);
    }
  };

  const handleVerify = async (batchId: string) => {
    if (!token) return;
    setVerifying(batchId);
    try {
      const result = await adminVerifyAuditBatch(token, batchId);
      setVerifications((prev) => ({
        ...prev,
        [batchId]: { batchId, result, error: null },
      }));
    } catch (e) {
      setVerifications((prev) => ({
        ...prev,
        [batchId]: { batchId, result: null, error: e instanceof Error ? e.message : "Verify failed" },
      }));
    } finally {
      setVerifying(null);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-brand-600 bg-transparent">
        <div className="flex flex-col items-center animate-pulse">
          <div className="w-12 h-12 border-4 border-brand-200 border-t-brand-600 rounded-full animate-spin mb-4"></div>
          <span className="font-semibold tracking-wide uppercase text-sm">Initializing System</span>
        </div>
      </div>
    );
  }

  if (!user?.is_admin) {
    return null;
  }

  return (
    <div className="min-h-screen px-6 py-10 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Audit Batch Sealing</h1>
          <p className="text-sm text-gray-500 mt-1">
            Seal ranges of the audit hash chain into Merkle batches and verify
            their external RFC 3161 timestamp anchors (spec 1.20.15-16).
          </p>
        </div>
        <Link href="/admin" className="text-sm text-indigo-600 hover:text-indigo-800">
          ← Admin
        </Link>
      </div>

      {notice && (
        <div className="mb-6 rounded-lg border border-emerald-300 bg-emerald-50 text-emerald-800 px-4 py-3 text-sm">
          {notice}
        </div>
      )}
      {error && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 text-red-700 px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Seal form */}
      <div className="glass-card rounded-2xl p-6 mb-8">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Seal a batch</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1" htmlFor="from-seq">
              From sequence
            </label>
            <input
              id="from-seq"
              type="number"
              min={1}
              value={fromSequence}
              onChange={(e) => setFromSequence(e.target.value)}
              placeholder="e.g. 1"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1" htmlFor="to-seq">
              To sequence
            </label>
            <input
              id="to-seq"
              type="number"
              min={1}
              value={toSequence}
              onChange={(e) => setToSequence(e.target.value)}
              placeholder="e.g. 500"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1" htmlFor="tenant-id">
              Tenant UUID (optional)
            </label>
            <input
              id="tenant-id"
              type="text"
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
              placeholder="defaults to your organization"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
            />
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={handleSeal}
            disabled={sealing}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {sealing ? "Sealing…" : "Seal batch"}
          </button>
          <span className="text-xs text-gray-400">
            Batches cannot be re-sealed over already-batched events (409).
          </span>
        </div>
      </div>

      {/* Sealed batches */}
      {sealed.length === 0 ? (
        <div className="glass-card rounded-2xl p-8 text-center text-sm text-gray-500">
          No batches sealed in this session yet. Seal a range above — batches
          seal here also run automatically via the scheduled worker.
        </div>
      ) : (
        <div className="space-y-4">
          {sealed.map((batch) => {
            const badge = anchorBadge[batch.anchor_status] ?? {
              label: batch.anchor_status,
              cls: "bg-gray-100 text-gray-700 border-gray-300",
            };
            const verification = verifications[batch.id];
            return (
              <div key={batch.id} className="glass-card rounded-2xl p-6">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className={`text-xs font-medium border rounded-full px-2.5 py-0.5 ${badge.cls}`}>
                      {badge.label}
                    </span>
                    <span className="text-sm font-semibold text-gray-900">
                      Sequences {batch.first_sequence}–{batch.last_sequence}
                    </span>
                    <span className="text-xs text-gray-500">{batch.leaf_count} events</span>
                  </div>
                  <button
                    onClick={() => handleVerify(batch.id)}
                    disabled={verifying === batch.id}
                    className="px-3 py-1.5 text-xs font-medium text-indigo-700 border border-indigo-300 rounded-lg hover:bg-indigo-50 disabled:opacity-50"
                  >
                    {verifying === batch.id ? "Verifying…" : "Verify"}
                  </button>
                </div>

                <div className="text-xs text-gray-500 space-y-1">
                  <div className="font-mono break-all">
                    root: {batch.root_hash}
                  </div>
                  {batch.anchored_at && (
                    <div>anchored at: {new Date(batch.anchored_at).toLocaleString()}</div>
                  )}
                </div>

                {verification?.error && (
                  <div className="mt-3 text-xs text-red-600">{verification.error}</div>
                )}
                {verification?.result && (
                  <div className="mt-3 rounded-lg border p-3 text-xs space-y-1 bg-white/50">
                    <div>
                      <span className="font-medium">Merkle root:</span>{" "}
                      {verification.result.merkle.valid ? (
                        <span className="text-emerald-700">valid — matches live events</span>
                      ) : (
                        <span className="text-red-700">
                          INVALID — {verification.result.merkle.reason ?? "root mismatch"}
                        </span>
                      )}
                    </div>
                    {verification.result.anchor ? (
                      <div>
                        <span className="font-medium">RFC 3161 anchor:</span>{" "}
                        {verification.result.anchor.valid ? (
                          <span className="text-emerald-700">
                            token attests this root (TSA time{" "}
                            {verification.result.anchor.gen_time ?? "unknown"})
                          </span>
                        ) : (
                          <span className="text-red-700">
                            {verification.result.anchor.reason ?? "token does not match"}
                          </span>
                        )}
                      </div>
                    ) : (
                      <div className="text-gray-500">
                        No external anchor token on this batch.
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
