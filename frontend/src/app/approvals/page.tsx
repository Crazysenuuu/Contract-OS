"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

interface ApprovalItem {
  id: string;
  agreement_id: string;
  agreement_title: string;
  requested_by_name: string;
  requested_at: string;
  deadline: string | null;
  level: number;
  status: string;
  notes: string | null;
}

interface PagedResult<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

const statusBadge: Record<string, string> = {
  pending: "bg-amber-100 text-amber-800 border border-amber-200",
  approved: "bg-emerald-100 text-emerald-800 border border-emerald-200",
  rejected: "bg-red-100 text-red-800 border border-red-200",
  withdrawn: "bg-gray-100 text-gray-500 border border-gray-200",
};

export default function ApprovalsPage() {
  const { token } = useAuth();
  const router = useRouter();
  const [items, setItems] = useState<ApprovalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actingOn, setActingOn] = useState<string | null>(null);
  const [decisionNote, setDecisionNote] = useState("");
  const [showDecisionModal, setShowDecisionModal] = useState<{
    item: ApprovalItem;
    action: "approve" | "reject";
  } | null>(null);

  // No leading setLoading(true): on first mount `loading` already starts as
  // true, and on refresh keeping the list visible avoids a spinner flash.
  // All setState calls live in promise callbacks (asynchronous).
  const load = useCallback(() => {
    if (!token) return;
    fetch("/api/v1/approvals?status=pending&page_size=50", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json() as Promise<PagedResult<ApprovalItem>>;
      })
      .then((d) => setItems(d.items))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load approvals"))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (!token) { router.push("/login"); return; }
    load();
  }, [token, router, load]);

  const handleDecision = async (action: "approve" | "reject") => {
    if (!showDecisionModal || !token) return;
    const { item } = showDecisionModal;
    setActingOn(item.id);
    try {
      const r = await fetch(`/api/v1/approvals/${item.id}/${action}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ notes: decisionNote }),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      setShowDecisionModal(null);
      setDecisionNote("");
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to record decision");
    } finally {
      setActingOn(null);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Pending Approvals</h1>
            <p className="text-gray-500 text-sm mt-1">
              Agreements waiting for your decision
            </p>
          </div>
          <span className="inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold bg-amber-100 text-amber-800 border border-amber-200">
            {items.length} pending
          </span>
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-20">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-emerald-100 flex items-center justify-center">
              <svg className="w-8 h-8 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-gray-500">All caught up — no pending approvals.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {items.map((item) => (
              <div
                key={item.id}
                className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 hover:shadow-md transition-shadow"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusBadge[item.status] ?? "bg-gray-100 text-gray-600"}`}
                      >
                        {item.status}
                      </span>
                      <span className="text-xs text-gray-400">Level {item.level}</span>
                    </div>
                    <h2
                      className="text-base font-semibold text-gray-900 truncate cursor-pointer hover:text-blue-600"
                      onClick={() => router.push(`/agreements/${item.agreement_id}`)}
                    >
                      {item.agreement_title}
                    </h2>
                    <p className="text-sm text-gray-500 mt-0.5">
                      Requested by <span className="font-medium text-gray-700">{item.requested_by_name}</span>
                      {" · "}
                      {new Date(item.requested_at).toLocaleDateString()}
                    </p>
                    {item.deadline && (
                      <p className="text-xs text-red-600 mt-1">
                        Due by {new Date(item.deadline).toLocaleDateString()}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      id={`reject-${item.id}`}
                      onClick={() => setShowDecisionModal({ item, action: "reject" })}
                      className="px-3 py-1.5 rounded-lg text-sm font-medium text-red-600 border border-red-200 hover:bg-red-50 transition-colors"
                    >
                      Reject
                    </button>
                    <button
                      id={`approve-${item.id}`}
                      onClick={() => setShowDecisionModal({ item, action: "approve" })}
                      className="px-3 py-1.5 rounded-lg text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 transition-colors"
                    >
                      Approve
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Decision modal */}
      {showDecisionModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6">
            <h3 className="text-lg font-bold text-gray-900 mb-1 capitalize">
              {showDecisionModal.action} Agreement
            </h3>
            <p className="text-sm text-gray-500 mb-4">
              {showDecisionModal.item.agreement_title}
            </p>
            <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="decision-note">
              Notes (optional)
            </label>
            <textarea
              id="decision-note"
              rows={3}
              value={decisionNote}
              onChange={(e) => setDecisionNote(e.target.value)}
              placeholder="Add a comment..."
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            />
            <div className="flex gap-3 mt-4">
              <button
                onClick={() => { setShowDecisionModal(null); setDecisionNote(""); }}
                className="flex-1 px-4 py-2 rounded-lg text-sm font-medium text-gray-700 border border-gray-200 hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
              <button
                id="confirm-decision-btn"
                onClick={() => handleDecision(showDecisionModal.action)}
                disabled={!!actingOn}
                className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors ${
                  showDecisionModal.action === "approve"
                    ? "bg-emerald-600 hover:bg-emerald-700"
                    : "bg-red-600 hover:bg-red-700"
                } disabled:opacity-50`}
              >
                {actingOn ? "Saving…" : `Confirm ${showDecisionModal.action}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
