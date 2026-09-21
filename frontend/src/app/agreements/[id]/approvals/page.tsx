"use client";

import { Suspense, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  getApprovalContext,
  getApprovalForAgreement,
  startApproval,
  makeApprovalDecision,
  listApprovalDefinitions,
  type ApprovalContext,
  type ApprovalRecordInfo,
} from "@/lib/api";

export const dynamic = "force-dynamic";

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  internal_review: "bg-blue-100 text-blue-800",
  pending_approval: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  negotiation: "bg-yellow-100 text-yellow-800",
  sent: "bg-purple-100 text-purple-800",
};

const stepLabels: Record<string, string> = {
  drafting: "Drafting",
  legal_review: "Legal review",
  party_a_confirmation: "Party confirmation",
  confirmed: "Confirmed",
  released: "Released",
};

function ApprovalWorkspace() {
  const { id } = useParams<{ id: string }>();
  const { token } = useAuth();

  const [context, setContext] = useState<ApprovalContext | null>(null);
  const [record, setRecord] = useState<ApprovalRecordInfo | null>(null);
  const [definitions, setDefinitions] = useState<
    Array<{ id: string; name: string }>
  >([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [action, setAction] = useState<
    "start" | "confirm" | "rejected" | null
  >(null);
  const [comment, setComment] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [approved, setApproved] = useState(false);

  useEffect(() => {
    if (!token || !id) return;
    Promise.all([
      getApprovalContext(token, id),
      getApprovalForAgreement(token, id).catch(() => null),
      listApprovalDefinitions(token).catch(() => []),
    ])
      .then(([ctx, rec, defs]) => {
        setContext(ctx);
        setRecord(rec);
        setDefinitions(defs);
      })
      .catch((e) => setError(e.message || "Failed to load approval context"))
      .finally(() => setLoading(false));
  }, [token, id]);

  const legalConfirmed = context?.legal_review.status === "confirmed";
  const canApprove = context?.viewer.can_approve === true;
  const approvalPending = record != null && record.status === "in_progress";

  const refresh = async () => {
    if (!token || !id) return;
    const [ctx, rec] = await Promise.all([
      getApprovalContext(token, id),
      getApprovalForAgreement(token, id).catch(() => null),
    ]);
    setContext(ctx);
    setRecord(rec);
  };

  const handleStart = async () => {
    if (!token || !id) return;
    setBusy(true);
    setError("");
    try {
      const started = await startApproval(token, id, {
        definition_id: definitions[0]?.id ?? null,
        approval_type: "legal_review",
      });
      // Started legal review; the workspace now waits for counsel.
      setRecord(started as ApprovalRecordInfo);
      await refresh();
    } catch (e) {
      setError((e as Error).message || "Failed to start approval");
    } finally {
      setBusy(false);
    }
  };

  const decide = async (decision: "approved" | "rejected") => {
    if (!token || !id || !record) return;
    setBusy(true);
    setError("");
    setMfaRequired(false);
    try {
      await makeApprovalDecision(token, id, record.id, {
        decision,
        comment: comment || null,
        mfa_code: mfaCode || null,
      });
      setComment("");
      setMfaCode("");
      setAction(null);
      if (decision === "approved") {
        setApproved(true);
      }
      await refresh();
    } catch (e) {
      const msg = (e as Error).message || "Decision failed";
      if (msg.includes("MFA code required")) {
        setMfaRequired(true);
      }
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  if (!context) {
    return (
      <div className="text-center py-12 text-gray-500">
        {error || "No approval context available"}
      </div>
    );
  }

  const changes = context.changes;
  const hasChanges =
    changes.added > 0 || changes.modified > 0 || changes.removed > 0;
  const awaitingCounsel = !legalConfirmed;

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h1 className="text-2xl font-medium text-gray-900">
            Agreement Approval
          </h1>
          <p className="text-sm text-gray-500">
            Each confirmation is recorded against the exact version reviewed.
          </p>
        </div>
        <Link
          href={`/agreements/${id}`}
          className="text-sm text-blue-600 hover:text-blue-800"
        >
          ← Back to agreement
        </Link>
      </div>

      <div className="bg-white shadow rounded-lg overflow-hidden">
        {/* Header */}
        <div className="border-b border-gray-200 px-6 py-5">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-medium text-gray-900">
              {context.agreement.title}
            </h2>
            <span
              className={`text-xs px-2 py-0.5 rounded ${
                statusColors[context.agreement.status] ||
                "bg-gray-100 text-gray-800"
              }`}
            >
              {context.agreement.status.replace(/_/g, " ")}
            </span>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            Workflow step:{" "}
            <span className="font-medium text-gray-700">
              {stepLabels[context.workflow.current_step] ||
                context.workflow.current_step}
            </span>
          </p>
        </div>

        {/* Exact version being reviewed (spec 2.05 §4-5) */}
        <div className="border-b border-gray-200 px-6 py-4">
          <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            Exact version under review
          </h3>
          {context.version ? (
            <div className="mt-1 flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <span className="text-sm font-medium text-gray-900">
                Version {context.version.version_number}
              </span>
              <span className="text-xs font-mono text-gray-500">
                SHA-256 {context.version.content_hash.slice(0, 16)}…
              </span>
            </div>
          ) : (
            <span className="text-sm text-gray-500">
              No current version rendered yet
            </span>
          )}
        </div>

        {/* Legal review gate (spec 2.05 §6) */}
        <div className="border-b border-gray-200 px-6 py-4">
          <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            Reviewed by counsel
          </h3>
          {legalConfirmed ? (
            <div className="mt-1 flex items-center gap-2">
              <span className="flex h-2 w-2 rounded-full bg-green-500" />
              <span className="text-sm text-green-700">
                Confirmed{" "}
                {context.legal_review.confirmed_at
                  ? new Date(
                      context.legal_review.confirmed_at
                    ).toLocaleDateString()
                  : ""}
              </span>
            </div>
          ) : (
            <div className="mt-1 flex items-center gap-2">
              <span className="flex h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
              <span className="text-sm text-amber-700">
                Waiting for counsel
              </span>
            </div>
          )}
          {awaitingCounsel && (
            <p className="mt-1 text-xs text-gray-500">
              A party representative cannot confirm final approval until the
              lawyer confirmation is recorded.
            </p>
          )}
        </div>

        {/* Change summary (spec 2.05 §1) */}
        <div className="border-b border-gray-200 px-6 py-4">
          <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            Changes
          </h3>
          {hasChanges ? (
            <div className="mt-1 flex gap-4 text-sm">
              {[
                ["modified", changes.modified, "bg-yellow-100 text-yellow-800"],
                ["added", changes.added, "bg-green-100 text-green-800"],
                ["removed", changes.removed, "bg-red-100 text-red-800"],
              ].map(([label, count, cls]) => (
                <span
                  key={label as string}
                  className={`text-xs px-2 py-0.5 rounded ${cls}`}
                >
                  {(count as number)} {label}
                </span>
              ))}
            </div>
          ) : (
            <span className="text-sm text-gray-500">
              No accepted changes
            </span>
          )}
          <div className="mt-3 flex gap-3 text-sm">
            <Link
              href={`/agreements/${id}/negotiate?id=${id}`}
              className="text-blue-600 hover:text-blue-800"
            >
              Review document / View all changes →
            </Link>
          </div>
        </div>

        {/* Actions */}
        <div className="px-6 py-5">
          {error && (
            <div className="mb-3 rounded bg-red-50 text-red-700 text-sm px-3 py-2">
              {error}
            </div>
          )}
          {approved && (
            <div className="mb-3 rounded bg-green-50 text-green-700 text-sm px-3 py-2">
              This version has been confirmed and the agreement advanced.
            </div>
          )}

          {!record && (
            <div>
              <p className="text-sm text-gray-600 mb-3">
                No approval process is active for this agreement yet.
                {canApprove
                  ? " Start the legal review approval to begin the workflow."
                  : " You do not have approval capability for this agreement."}
              </p>
              {canApprove && (
                <button
                  onClick={handleStart}
                  disabled={busy || definitions.length === 0}
                  className="text-sm px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {busy ? "Starting..." : "Start approval"}
                </button>
              )}
            </div>
          )}

          {record && approvalPending && (
            <div>
              <div className="flex items-center gap-2 mb-3 text-sm text-gray-500">
                <span className="flex h-2 w-2 rounded-full bg-blue-500" />
                Approval {record.approval_type.replace(/_/g, " ")} is in
                progress
              </div>

              {!canApprove ? (
                <p className="text-sm text-gray-500">
                  You do not have approval capability on this agreement.
                </p>
              ) : awaitingCounsel ? (
                <p className="text-sm text-amber-700">
                  Awaiting counsel confirmation — actions are disabled until
                  legal review is recorded.
                </p>
              ) : (
                <div className="space-y-3">
                  {mfaRequired && (
                    <input
                      type="text"
                      inputMode="numeric"
                      placeholder="MFA code (TOTP)"
                      value={mfaCode}
                      onChange={(e) => setMfaCode(e.target.value)}
                      className="w-full text-sm border border-gray-300 rounded px-2 py-1.5"
                    />
                  )}
                  {action === "rejected" && (
                    <textarea
                      rows={3}
                      placeholder="What changes are required before this version can be approved?"
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      className="w-full text-sm border border-gray-300 rounded px-2 py-1.5"
                    />
                  )}
                  <div className="flex gap-3">
                    <button
                      onClick={() =>
                        action === "rejected"
                          ? decide("rejected")
                          : setAction("rejected")
                      }
                      disabled={busy}
                      className="text-sm px-4 py-2 border border-gray-300 rounded text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    >
                      {busy && action === "rejected"
                        ? "Submitting..."
                        : action === "rejected"
                        ? "Submit request"
                        : "Request changes"}
                    </button>
                    <button
                      onClick={() =>
                        action === "confirm" ? decide("approved") : setAction("confirm")
                      }
                      disabled={busy}
                      className="text-sm px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                    >
                      {busy && action === "confirm"
                        ? "Confirming..."
                        : action === "confirm"
                        ? "Confirm version"
                        : "Confirm version"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {record && !approvalPending && (
            <p className="text-sm text-gray-500">
              Approval status:{" "}
              <span className="font-medium">{record.status.replace(/_/g, " ")}</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ApprovalPage() {
  return (
    <Suspense
      fallback={
        <div className="text-center py-12 text-gray-500">Loading...</div>
      }
    >
      <ApprovalWorkspace />
    </Suspense>
  );
}