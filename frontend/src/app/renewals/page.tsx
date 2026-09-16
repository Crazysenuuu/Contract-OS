"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

interface RenewalItem {
  id: string;
  agreement_id: string;
  agreement_title: string;
  expiry_date: string;
  renewal_window_days: number;
  status: string;
  auto_renew: boolean;
  notice_sent_at: string | null;
  renewed_at: string | null;
}

function daysUntil(dateStr: string): number {
  const diff = new Date(dateStr).getTime() - Date.now();
  return Math.ceil(diff / (1000 * 60 * 60 * 24));
}

function urgencyBadge(days: number): string {
  if (days < 0) return "bg-red-100 text-red-800 border border-red-200";
  if (days <= 14) return "bg-red-50 text-red-700 border border-red-200";
  if (days <= 30) return "bg-amber-50 text-amber-700 border border-amber-200";
  return "bg-gray-100 text-gray-600 border border-gray-200";
}

export default function RenewalsPage() {
  const { token } = useAuth();
  const router = useRouter();
  const [items, setItems] = useState<RenewalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [initiating, setInitiating] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "urgent" | "auto_renew">("all");

  // No leading setLoading(true): avoids a synchronous setState cascade from
  // the mount effect; all updates happen in async promise callbacks.
  const load = useCallback(() => {
    if (!token) return;
    fetch("/api/v1/renewals?page_size=100", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      })
      .then((d) => setItems(d.items ?? d))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load renewals"))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (!token) { router.push("/login"); return; }
    load();
  }, [token, router, load]);

  const handleInitiate = async (item: RenewalItem) => {
    if (!token) return;
    setInitiating(item.id);
    try {
      const r = await fetch(`/api/v1/renewals/${item.id}/initiate`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      });
      if (!r.ok) throw new Error(`${r.status}`);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to initiate renewal");
    } finally {
      setInitiating(null);
    }
  };

  const filteredItems = items.filter((i) => {
    const days = daysUntil(i.expiry_date);
    if (filter === "urgent") return days <= 30;
    if (filter === "auto_renew") return i.auto_renew;
    return true;
  });

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Renewals</h1>
            <p className="text-gray-500 text-sm mt-1">
              Track and initiate agreement renewals
            </p>
          </div>
        </div>

        {/* Filter tabs */}
        <div className="flex gap-2 mb-6">
          {(["all", "urgent", "auto_renew"] as const).map((f) => (
            <button
              key={f}
              id={`filter-${f}`}
              onClick={() => setFilter(f)}
              className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
                filter === f
                  ? "bg-blue-600 text-white"
                  : "bg-white text-gray-600 border border-gray-200 hover:bg-gray-50"
              }`}
            >
              {f === "all" ? "All" : f === "urgent" ? "≤30 days" : "Auto-renew"}
            </button>
          ))}
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-20">
            <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-gray-400">No renewals match this filter.</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="text-left px-5 py-3 font-semibold text-gray-600">Agreement</th>
                  <th className="text-left px-5 py-3 font-semibold text-gray-600">Expires</th>
                  <th className="text-left px-5 py-3 font-semibold text-gray-600">Days left</th>
                  <th className="text-left px-5 py-3 font-semibold text-gray-600">Status</th>
                  <th className="text-left px-5 py-3 font-semibold text-gray-600">Auto</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredItems.map((item) => {
                  const days = daysUntil(item.expiry_date);
                  return (
                    <tr key={item.id} className="hover:bg-gray-50 transition-colors">
                      <td className="px-5 py-4">
                        <span
                          className="font-medium text-gray-900 cursor-pointer hover:text-blue-600"
                          onClick={() => router.push(`/agreements/${item.agreement_id}`)}
                        >
                          {item.agreement_title}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-gray-600">
                        {new Date(item.expiry_date).toLocaleDateString()}
                      </td>
                      <td className="px-5 py-4">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs ${urgencyBadge(days)}`}>
                          {days < 0 ? `${Math.abs(days)}d overdue` : `${days}d`}
                        </span>
                      </td>
                      <td className="px-5 py-4">
                        <span className="capitalize text-gray-600">{item.status}</span>
                      </td>
                      <td className="px-5 py-4">
                        {item.auto_renew ? (
                          <span className="text-emerald-600 font-medium">Yes</span>
                        ) : (
                          <span className="text-gray-400">No</span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-right">
                        {!item.renewed_at && item.status !== "renewed" && (
                          <button
                            id={`initiate-renewal-${item.id}`}
                            onClick={() => handleInitiate(item)}
                            disabled={initiating === item.id}
                            className="px-3 py-1.5 rounded-lg text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 transition-colors disabled:opacity-50"
                          >
                            {initiating === item.id ? "…" : "Initiate renewal"}
                          </button>
                        )}
                        {item.renewed_at && (
                          <span className="text-xs text-emerald-600 font-medium">Renewed</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
