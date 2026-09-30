"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listDmcaNotices,
  updateDmcaNotice,
  DmcaNotice,
} from "@/lib/api";

/**
 * DMCA notice queue (admin only) — the processing side of the § 512
 * designated-agent workflow. Notices arrive through the public intake
 * endpoint; this panel records the decision (expeditious removal,
 * rejection of invalid notices, or restoration after a counter-notice's
 * 10–14 business day window) with an audit note.
 */
export default function DmcaAdminPage() {
  const { token, user } = useAuth();
  const isAdmin = !!user?.is_admin;
  const [notices, setNotices] = useState<DmcaNotice[]>([]);
  const [filter, setFilter] = useState<"all" | DmcaNotice["status"]>("all");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notesFor, setNotesFor] = useState<string | null>(null);
  const [noteText, setNoteText] = useState("");

  const reload = useCallback(() => {
    if (!token) return;
    setError(null);
    listDmcaNotices(token)
      .then(setNotices)
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Failed to load notices")
      );
  }, [token]);

  useEffect(() => {
    if (!token) return;
    queueMicrotask(() => reload());
  }, [token, reload]);

  const process = async (
    id: string,
    status: "action_taken" | "rejected" | "restored",
    admin_note?: string
  ) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await updateDmcaNotice(token!, id, { status, admin_note });
      setNotice(`Notice marked "${status.replace("_", " ")}".`);
      setNotesFor(null);
      setNoteText("");
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update notice");
    } finally {
      setBusy(false);
    }
  };

  if (!isAdmin) {
    return (
      <div className="p-8 text-sm text-rose-700">
        Admin access required.
      </div>
    );
  }

  const visible =
    filter === "all" ? notices : notices.filter((n) => n.status === filter);

  const statusBadge = (s: DmcaNotice["status"]) => {
    const cls: Record<DmcaNotice["status"], string> = {
      received: "bg-amber-100 text-amber-800 border-amber-200",
      action_taken: "bg-emerald-100 text-emerald-800 border-emerald-200",
      rejected: "bg-gray-100 text-gray-600 border-gray-200",
      restored: "bg-blue-100 text-blue-800 border-blue-200",
    };
    return (
      <span
        className={`px-2 py-0.5 text-[10px] uppercase font-bold rounded-full border ${cls[s]}`}
      >
        {s.replace("_", " ")}
      </span>
    );
  };

  return (
    <div className="p-8 max-w-6xl">
      <div className="mb-6">
        <Link href="/admin" className="text-sm text-gray-500 hover:text-gray-700">
          ← Admin
        </Link>
        <h1 className="text-2xl font-bold text-gray-900 mt-1">
          DMCA Notice Queue
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Takedown notices and counter-notifications (17 U.S.C. § 512).
          Removal must be expeditious; restorations follow the § 512(g)
          counter-notice window.
        </p>
      </div>

      {error && (
        <div className="mb-4 px-4 py-3 rounded-md bg-rose-50 border border-rose-200 text-sm text-rose-700">
          {error}
        </div>
      )}
      {notice && (
        <div className="mb-4 px-4 py-3 rounded-md bg-emerald-50 border border-emerald-200 text-sm text-emerald-800">
          {notice}
        </div>
      )}

      <div className="mb-4 flex gap-2 flex-wrap">
        {(["all", "received", "action_taken", "rejected", "restored"] as const).map(
          (f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1.5 text-xs font-medium rounded-full border ${
                filter === f
                  ? "bg-gray-900 text-white border-gray-900"
                  : "bg-white text-gray-600 border-gray-200 hover:bg-gray-50"
              }`}
            >
              {f === "all"
                ? `All (${notices.length})`
                : `${f.replace("_", " ")} (${notices.filter((n) => n.status === f).length})`}
            </button>
          )
        )}
      </div>

      {visible.length === 0 ? (
        <div className="bg-white shadow rounded-lg p-10 text-center text-sm text-gray-500">
          No notices{filter !== "all" ? ` with status "${filter.replace("_", " ")}"` : ""}.
        </div>
      ) : (
        <div className="space-y-4">
          {visible.map((n) => (
            <div key={n.id} className="bg-white shadow rounded-lg p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`px-2 py-0.5 text-[10px] uppercase font-bold rounded-full ${
                        n.kind === "takedown"
                          ? "bg-rose-100 text-rose-700"
                          : "bg-indigo-100 text-indigo-700"
                      }`}
                    >
                      {n.kind}
                    </span>
                    {statusBadge(n.status)}
                    <span className="text-xs text-gray-400">
                      received {new Date(n.received_at).toLocaleString()}
                    </span>
                  </div>
                  <p className="text-sm font-medium text-gray-900 mt-2">
                    {n.reporter_name}{" "}
                    <span className="text-gray-400 font-normal">
                      &lt;{n.reporter_email}&gt;
                    </span>
                  </p>
                  <p className="text-sm text-gray-700 mt-1">
                    <span className="font-medium">Work:</span> {n.work_description}
                  </p>
                  <p className="text-sm text-gray-700 mt-0.5 break-all">
                    <span className="font-medium">Location:</span>{" "}
                    {n.material_location}
                  </p>
                  {n.admin_note && (
                    <p className="text-xs text-gray-500 mt-2 border-l-2 border-gray-200 pl-2">
                      Note: {n.admin_note}
                    </p>
                  )}
                </div>
                {n.status === "received" && (
                  <div className="shrink-0">
                    {notesFor === n.id ? (
                      <div className="w-72 space-y-2">
                        <textarea
                          value={noteText}
                          onChange={(e) => setNoteText(e.target.value)}
                          placeholder="Decision note (audit trail)…"
                          rows={3}
                          className="w-full rounded-md border border-gray-300 shadow-sm text-sm"
                        />
                        <div className="flex gap-2 flex-wrap">
                          <button
                            disabled={busy}
                            onClick={() =>
                              process(n.id, "action_taken", noteText || undefined)
                            }
                            className="px-3 py-1.5 text-xs font-bold rounded-md bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
                          >
                            Action taken
                          </button>
                          <button
                            disabled={busy}
                            onClick={() =>
                              process(n.id, "rejected", noteText || undefined)
                            }
                            className="px-3 py-1.5 text-xs font-bold rounded-md bg-gray-600 text-white hover:bg-gray-700 disabled:opacity-50"
                          >
                            Reject
                          </button>
                          <button
                            disabled={busy}
                            onClick={() =>
                              process(n.id, "restored", noteText || undefined)
                            }
                            className="px-3 py-1.5 text-xs font-bold rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                          >
                            Restored
                          </button>
                          <button
                            onClick={() => {
                              setNotesFor(null);
                              setNoteText("");
                            }}
                            className="px-3 py-1.5 text-xs font-medium rounded-md border border-gray-300 text-gray-600 hover:bg-gray-50"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => setNotesFor(n.id)}
                        className="px-4 py-2 text-sm font-medium text-white bg-gray-900 rounded-md hover:bg-gray-800"
                      >
                        Process…
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
