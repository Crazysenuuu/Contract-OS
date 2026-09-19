"use client";

import { useCallback, useEffect, useState } from "react";
import {
  activateAmendment,
  cancelTermination,
  completeTermination,
  createAmendment,
  getSignatureProgress,
  initiateTermination,
  issueTerminationNotice,
  listAmendments,
  listTerminations,
  type Amendment,
  type SignatureProgress,
  type Termination,
} from "@/lib/api";

interface Props {
  token: string;
  agreementId: string;
  status: string;
  onChanged?: () => void;
}

const IN_FORCE = new Set(["executed", "active", "expiring", "renewed"]);
const SIGNING = new Set(["ready_for_signature", "signing", "partially_signed", "sent", "viewed", "approved"]);

const REASON_CODES = [
  { value: "convenience", label: "Termination for convenience" },
  { value: "breach", label: "Material breach" },
  { value: "insolvency", label: "Insolvency of counterparty" },
  { value: "mutual", label: "Mutual agreement" },
  { value: "force_majeure", label: "Force majeure" },
];

const statusTone: Record<string, string> = {
  draft: "bg-gray-100 text-gray-700",
  proposed: "bg-amber-100 text-amber-800",
  active: "bg-green-100 text-green-800",
  initiated: "bg-amber-100 text-amber-800",
  notice_served: "bg-blue-100 text-blue-800",
  cure_period: "bg-purple-100 text-purple-800",
  completed: "bg-red-100 text-red-800",
  cancelled: "bg-gray-100 text-gray-600",
};

function Badge({ value }: { value: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
        statusTone[value] ?? "bg-gray-100 text-gray-700"
      }`}
    >
      {value.replace(/_/g, " ")}
    </span>
  );
}

export default function AgreementLifecyclePanel({ token, agreementId, status, onChanged }: Props) {
  const [progress, setProgress] = useState<SignatureProgress | null>(null);
  const [amendments, setAmendments] = useState<Amendment[]>([]);
  const [terminations, setTerminations] = useState<Termination[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [showAmend, setShowAmend] = useState(false);
  const [amendForm, setAmendForm] = useState({ title: "", reason: "", section_key: "", new_text: "" });

  const [showTerminate, setShowTerminate] = useState(false);
  const [termForm, setTermForm] = useState({
    reason_code: "convenience",
    reason_detail: "",
    notice_period_days: "30",
    cure_required: false,
    cure_period_days: "14",
  });

  const load = useCallback(async () => {
    const [p, a, t] = await Promise.allSettled([
      getSignatureProgress(token, agreementId),
      listAmendments(token, agreementId),
      listTerminations(token, agreementId),
    ]);
    if (p.status === "fulfilled") setProgress(p.value);
    if (a.status === "fulfilled") setAmendments(a.value);
    if (t.status === "fulfilled") setTerminations(t.value);
  }, [token, agreementId]);

  useEffect(() => {
    // Defer so the effect body never triggers a synchronous setState cascade.
    queueMicrotask(() => load());
  }, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const submitAmendment = () =>
    run(async () => {
      await createAmendment(token, agreementId, {
        title: amendForm.title,
        reason: amendForm.reason || undefined,
        changes: [{ section_key: amendForm.section_key, change_type: "replace", new_text: amendForm.new_text }],
      });
      setAmendForm({ title: "", reason: "", section_key: "", new_text: "" });
      setShowAmend(false);
    });

  const submitTermination = () =>
    run(async () => {
      await initiateTermination(token, agreementId, {
        reason_code: termForm.reason_code,
        reason_detail: termForm.reason_detail || undefined,
        notice_period_days: termForm.notice_period_days ? Number(termForm.notice_period_days) : undefined,
        cure_required: termForm.cure_required,
        cure_period_days: termForm.cure_required && termForm.cure_period_days ? Number(termForm.cure_period_days) : undefined,
      });
      setShowTerminate(false);
    });

  const inForce = IN_FORCE.has(status);
  const openTermination = terminations.find((t) => !["completed", "cancelled"].includes(t.status));

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {/* Signature progress — spec §67 */}
      {(SIGNING.has(status) || IN_FORCE.has(status)) && progress && (
        <section className="rounded-lg bg-white p-6 shadow">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-medium text-gray-900">Signature progress</h2>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                progress.all_signed ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
              }`}
            >
              {progress.all_signed ? "All required signers complete" : "Awaiting signatures"}
            </span>
          </div>
          <div className="grid grid-cols-3 gap-4 text-center">
            <div className="rounded-md bg-gray-50 p-3">
              <div className="text-2xl font-semibold text-gray-900">{progress.internal_signatures}</div>
              <div className="text-xs text-gray-500">Internal</div>
            </div>
            <div className="rounded-md bg-gray-50 p-3">
              <div className="text-2xl font-semibold text-gray-900">
                {progress.signed_external}/{progress.required_external}
              </div>
              <div className="text-xs text-gray-500">Counterparty</div>
            </div>
            <div className="rounded-md bg-gray-50 p-3">
              <div className="text-2xl font-semibold text-gray-900">{progress.missing_external.length}</div>
              <div className="text-xs text-gray-500">Outstanding</div>
            </div>
          </div>
          {progress.missing_external.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm text-gray-600">
              {progress.missing_external.map((email) => (
                <li key={email} className="flex items-center gap-2">
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                  Waiting for <span className="font-medium">{email}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* Amendments — spec §36 */}
      <section className="rounded-lg bg-white p-6 shadow">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-medium text-gray-900">Amendments</h2>
            <p className="text-xs text-gray-500">
              Executed terms are immutable; changes are layered as numbered amendments.
            </p>
          </div>
          {inForce && (
            <button
              onClick={() => setShowAmend((v) => !v)}
              className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-blue-700"
            >
              {showAmend ? "Close" : "New amendment"}
            </button>
          )}
        </div>

        {showAmend && (
          <div className="mb-4 grid gap-3 rounded-md border border-gray-200 bg-gray-50 p-4 sm:grid-cols-2">
            <input
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Amendment title"
              value={amendForm.title}
              onChange={(e) => setAmendForm({ ...amendForm, title: e.target.value })}
            />
            <input
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Reason (optional)"
              value={amendForm.reason}
              onChange={(e) => setAmendForm({ ...amendForm, reason: e.target.value })}
            />
            <input
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Section key, e.g. payment_terms"
              value={amendForm.section_key}
              onChange={(e) => setAmendForm({ ...amendForm, section_key: e.target.value })}
            />
            <textarea
              className="rounded-md border border-gray-300 px-3 py-2 text-sm sm:col-span-2"
              rows={3}
              placeholder="New wording"
              value={amendForm.new_text}
              onChange={(e) => setAmendForm({ ...amendForm, new_text: e.target.value })}
            />
            <div className="sm:col-span-2">
              <button
                disabled={busy || !amendForm.title || !amendForm.section_key || !amendForm.new_text}
                onClick={submitAmendment}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {busy ? "Saving…" : "Propose amendment"}
              </button>
            </div>
          </div>
        )}

        {amendments.length === 0 ? (
          <p className="text-sm text-gray-500">No amendments.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {amendments.map((a) => (
              <li key={a.id} className="flex items-start justify-between gap-4 py-3">
                <div>
                  <div className="text-sm font-medium text-gray-900">
                    #{a.amendment_number} · {a.title}
                  </div>
                  <div className="mt-0.5 text-xs text-gray-500">
                    {a.reason || a.description || "—"}
                    {a.effective_date ? ` · effective ${a.effective_date}` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge value={a.status} />
                  {a.status !== "active" && (
                    <button
                      disabled={busy}
                      onClick={() => run(() => activateAmendment(token, agreementId, a.id))}
                      className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    >
                      Activate
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Terminations — spec §66 */}
      <section className="rounded-lg bg-white p-6 shadow">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-medium text-gray-900">Termination</h2>
            <p className="text-xs text-gray-500">Notice → cure period → completion, with an audit trail.</p>
          </div>
          {inForce && !openTermination && (
            <button
              onClick={() => setShowTerminate((v) => !v)}
              className="rounded-md border border-red-300 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700 transition hover:bg-red-100"
            >
              {showTerminate ? "Close" : "Initiate termination"}
            </button>
          )}
        </div>

        {showTerminate && (
          <div className="mb-4 grid gap-3 rounded-md border border-red-100 bg-red-50/40 p-4 sm:grid-cols-2">
            <select
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              value={termForm.reason_code}
              onChange={(e) => setTermForm({ ...termForm, reason_code: e.target.value })}
            >
              {REASON_CODES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
            <input
              type="number"
              min={0}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="Notice period (days)"
              value={termForm.notice_period_days}
              onChange={(e) => setTermForm({ ...termForm, notice_period_days: e.target.value })}
            />
            <textarea
              className="rounded-md border border-gray-300 px-3 py-2 text-sm sm:col-span-2"
              rows={2}
              placeholder="Details"
              value={termForm.reason_detail}
              onChange={(e) => setTermForm({ ...termForm, reason_detail: e.target.value })}
            />
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={termForm.cure_required}
                onChange={(e) => setTermForm({ ...termForm, cure_required: e.target.checked })}
              />
              Allow a cure period
            </label>
            {termForm.cure_required && (
              <input
                type="number"
                min={1}
                className="rounded-md border border-gray-300 px-3 py-2 text-sm"
                placeholder="Cure period (days)"
                value={termForm.cure_period_days}
                onChange={(e) => setTermForm({ ...termForm, cure_period_days: e.target.value })}
              />
            )}
            <div className="sm:col-span-2">
              <button
                disabled={busy}
                onClick={submitTermination}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {busy ? "Submitting…" : "Initiate"}
              </button>
            </div>
          </div>
        )}

        {terminations.length === 0 ? (
          <p className="text-sm text-gray-500">No termination on record.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {terminations.map((t) => {
              const open = !["completed", "cancelled"].includes(t.status);
              return (
                <li key={t.id} className="py-3">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="text-sm font-medium text-gray-900">
                        {REASON_CODES.find((r) => r.value === t.reason_code)?.label ?? t.reason_code}
                      </div>
                      <div className="mt-0.5 text-xs text-gray-500">
                        Initiated {new Date(t.initiated_at).toLocaleDateString()}
                        {t.notice_period_days != null ? ` · ${t.notice_period_days}-day notice` : ""}
                        {t.cure_deadline ? ` · cure by ${t.cure_deadline}` : ""}
                        {t.effective_date ? ` · effective ${t.effective_date}` : ""}
                      </div>
                    </div>
                    <Badge value={t.status} />
                  </div>
                  {open && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {!t.notice_served && (
                        <button
                          disabled={busy}
                          onClick={() => run(() => issueTerminationNotice(token, agreementId, t.id))}
                          className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                        >
                          Serve notice
                        </button>
                      )}
                      <button
                        disabled={busy}
                        onClick={() => run(() => completeTermination(token, agreementId, t.id))}
                        className="rounded-md bg-red-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
                      >
                        Complete termination
                      </button>
                      <button
                        disabled={busy}
                        onClick={() => run(() => cancelTermination(token, agreementId, t.id))}
                        className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                      >
                        Withdraw
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
