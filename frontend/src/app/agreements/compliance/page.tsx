"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  getAgreement,
  runComplianceCheck,
  listComplianceReports,
  listComplianceViolations,
  updateViolationStatus,
  getPolicyChecks,
  evaluateAgreementPolicy,
} from "@/lib/api";

type Report = Awaited<ReturnType<typeof listComplianceReports>>[number];
type Violation = Awaited<ReturnType<typeof listComplianceViolations>>[number];
type RunResult = Awaited<ReturnType<typeof runComplianceCheck>>;
type PolicyChecks = Awaited<ReturnType<typeof getPolicyChecks>>;

const severityStyles: Record<string, string> = {
  critical: "bg-rose-50 text-rose-700 border-rose-200",
  high: "bg-orange-50 text-orange-700 border-orange-200",
  medium: "bg-amber-50 text-amber-700 border-amber-200",
  low: "bg-gray-50 text-gray-600 border-gray-200",
};

function ComplianceInner() {
  const { token } = useAuth();
  const params = useSearchParams();
  const id = params.get("id");
  const [title, setTitle] = useState("");
  const [reports, setReports] = useState<Report[]>([]);
  const [violations, setViolations] = useState<Violation[]>([]);
  const [policyChecks, setPolicyChecks] = useState<PolicyChecks | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadViolations = useCallback(
    (agreementId: string) => {
      if (!token) return;
      listComplianceViolations(token, agreementId)
        .then(setViolations)
        .catch(() => setViolations([]));
    },
    [token]
  );

  const loadPolicy = useCallback(
    (agreementId: string) => {
      if (!token) return;
      getPolicyChecks(token, agreementId)
        .then(setPolicyChecks)
        .catch(() => setPolicyChecks(null));
    },
    [token]
  );

  useEffect(() => {
    if (!token || !id) return;
    void Promise.resolve().then(() => {
      setError(null);
      getAgreement(token, id)
        .then((a) => setTitle(a.title))
        .catch(() => {});
      listComplianceReports(token, id)
        .then(setReports)
        .catch(() => {});
      loadViolations(id);
      loadPolicy(id);
    });
  }, [token, id, loadViolations, loadPolicy]);

  const handleRunCheck = async () => {
    if (!token || !id) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result: RunResult = await runComplianceCheck(token, id);
      setNotice(
        `Check complete — score ${result.compliance_score}%, ${result.violations_found} violation(s) found`
      );
      const [reps, viols] = await Promise.all([
        listComplianceReports(token, id),
        listComplianceViolations(token, id),
      ]);
      setReports(reps);
      setViolations(viols);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Compliance check failed");
    } finally {
      setBusy(false);
    }
  };

  const handleEvaluatePolicy = async () => {
    if (!token || !id) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await evaluateAgreementPolicy(token, id);
      setNotice(
        `Policy evaluation: ${r.rules_evaluated} rules, ${r.rules_triggered} triggered (score ${r.compliance_score}%)`
      );
      loadPolicy(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Policy evaluation failed");
    } finally {
      setBusy(false);
    }
  };

  const handleViolationStatus = async (
    violationId: string,
    status: string
  ) => {
    if (!token || !id) return;
    setBusy(true);
    setError(null);
    try {
      await updateViolationStatus(token, id, violationId, {
        reviewer_status: status,
      });
      loadViolations(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update violation");
    } finally {
      setBusy(false);
    }
  };

  if (!id) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <p className="text-gray-500">
          No agreement selected.{" "}
          <Link href="/agreements" className="text-blue-600 hover:underline">
            Browse agreements
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <main className="max-w-5xl mx-auto px-4 py-8">
        <div className="mb-6">
          <Link
            href={`/agreements/${id}`}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            ← {title || "Agreement"}
          </Link>
          <h1 className="mt-2 text-2xl font-bold text-gray-900">
            Compliance Center
          </h1>
          <p className="mt-1 text-sm text-gray-600">
            Run policy checks, review violations, and track compliance history.
          </p>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-md bg-rose-50 border border-rose-200 text-sm text-rose-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-4 px-4 py-3 rounded-md bg-emerald-50 border border-emerald-200 text-sm text-emerald-700">
            {notice}
          </div>
        )}

        {/* Actions */}
        <div className="mb-8 flex flex-wrap gap-3">
          <button
            onClick={handleRunCheck}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {busy ? "Running…" : "▶ Run compliance check"}
          </button>
          <button
            onClick={handleEvaluatePolicy}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
          >
            Evaluate company policies
          </button>
        </div>

        {/* Policy checks summary */}
        {policyChecks && (
          <div className="mb-8 bg-white rounded-lg border border-gray-200 p-6">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">
              Company policy checks
            </h2>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div className="rounded-lg bg-emerald-50 p-3 text-center">
                <p className="text-2xl font-semibold text-emerald-700">
                  {policyChecks.summary.passed}
                </p>
                <p className="text-xs text-emerald-600">Passed</p>
              </div>
              <div className="rounded-lg bg-rose-50 p-3 text-center">
                <p className="text-2xl font-semibold text-rose-700">
                  {policyChecks.summary.failed}
                </p>
                <p className="text-xs text-rose-600">Failed</p>
              </div>
              <div className="rounded-lg bg-amber-50 p-3 text-center">
                <p className="text-2xl font-semibold text-amber-700">
                  {policyChecks.summary.warnings}
                </p>
                <p className="text-xs text-amber-600">Warnings</p>
              </div>
            </div>
            {policyChecks.warnings.length > 0 && (
              <ul className="space-y-2">
                {policyChecks.warnings.map((w) => (
                  <li
                    key={w.rule_id}
                    className="flex items-center gap-2 text-sm text-gray-700"
                  >
                    <span
                      className={`px-2 py-0.5 text-[10px] rounded-full font-medium ${
                        severityStyles[w.severity] ?? severityStyles.low
                      } border`}
                    >
                      {w.severity}
                    </span>
                    {w.rule_name}
                  </li>
                ))}
              </ul>
            )}
            {policyChecks.required_approvals.length > 0 && (
              <p className="mt-3 text-xs text-gray-500">
                Required approval roles:{" "}
                {policyChecks.required_approvals
                  .map((r) => r.role)
                  .join(", ")}
              </p>
            )}
          </div>
        )}

        {/* Violations */}
        <div className="mb-8 bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
          <div className="p-4">
            <h2 className="text-sm font-semibold text-gray-900">
              Violations ({violations.length})
            </h2>
          </div>
          {violations.length === 0 ? (
            <p className="p-4 text-sm text-gray-500">
              No violations recorded. Run a check to scan the agreement.
            </p>
          ) : (
            violations.map((v) => (
              <div key={v.id} className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span
                        className={`px-2 py-0.5 text-[10px] rounded-full font-medium border ${
                          severityStyles[v.severity] ?? severityStyles.low
                        }`}
                      >
                        {v.severity}
                      </span>
                      <span className="text-sm font-medium text-gray-900">
                        {v.violation_type.replace(/_/g, " ")}
                      </span>
                      <span className="text-xs text-gray-400">
                        {(v.confidence * 100).toFixed(0)}% confidence
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-gray-600">
                      {v.description}
                    </p>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    {["accepted", "rejected", "pending"].map((s) => (
                      <button
                        key={s}
                        onClick={() => handleViolationStatus(v.id, s)}
                        disabled={busy || v.reviewer_status === s}
                        className={`px-2 py-1 text-[10px] font-medium rounded-md border disabled:opacity-100 ${
                          v.reviewer_status === s
                            ? "bg-gray-800 text-white border-gray-800"
                            : "bg-white text-gray-600 border-gray-200 hover:bg-gray-50"
                        }`}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Report history */}
        <div className="bg-white rounded-lg border border-gray-200">
          <div className="p-4 border-b border-gray-100">
            <h2 className="text-sm font-semibold text-gray-900">
              Check history
            </h2>
          </div>
          {reports.length === 0 ? (
            <p className="p-4 text-sm text-gray-500">No checks run yet.</p>
          ) : (
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b border-gray-100">
                  <th className="px-4 py-2 font-medium">Score</th>
                  <th className="px-4 py-2 font-medium">Violations</th>
                  <th className="px-4 py-2 font-medium">Severities</th>
                  <th className="px-4 py-2 font-medium">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {reports.map((r) => (
                  <tr key={r.id}>
                    <td className="px-4 py-2 font-medium text-gray-900">
                      {r.compliance_score}%
                    </td>
                    <td className="px-4 py-2 text-gray-600">
                      {r.violations_found}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-500">
                      {r.critical_count}C / {r.high_count}H /{" "}
                      {r.medium_count}M / {r.low_count}L
                    </td>
                    <td className="px-4 py-2 text-gray-500 text-xs">
                      {new Date(r.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}

export default function CompliancePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-gray-50 flex items-center justify-center">
          <p className="text-gray-500">Loading…</p>
        </div>
      }
    >
      <ComplianceInner />
    </Suspense>
  );
}
