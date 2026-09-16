"use client";

import { useState } from "react";
import {
  getQualityCheck,
  type QualityFinding,
  type QualityReport,
} from "@/lib/api";

/**
 * Contract-quality findings panel (spec 77-81).
 *
 * Runs the deterministic lint engines server-side (undefined terms, broken
 * cross-references, percentage splits, date ordering, party/signatory
 * mismatches) and renders the findings with their exact evidence. Advisory
 * only — findings never block the workflow.
 */

const severityStyles: Record<string, string> = {
  high: "bg-red-50 border-red-200 text-red-800",
  medium: "bg-amber-50 border-amber-200 text-amber-800",
  low: "bg-gray-50 border-gray-200 text-gray-700",
};

const severityBadge: Record<string, string> = {
  high: "bg-red-100 text-red-700",
  medium: "bg-amber-100 text-amber-700",
  low: "bg-gray-100 text-gray-600",
};

const engineLabels: Record<string, string> = {
  undefined_term: "Undefined Terms",
  cross_reference: "Cross-References",
  numerical_consistency: "Numerical Consistency",
  date_consistency: "Date Consistency",
  party_consistency: "Party Consistency",
};

function FindingRow({ finding }: { finding: QualityFinding }) {
  const style = severityStyles[finding.severity] || severityStyles.low;
  return (
    <li className={`border rounded-md p-3 text-sm ${style}`}>
      <div className="flex items-center justify-between gap-2 mb-1">
        <span
          className={`px-1.5 py-0.5 text-xs font-semibold rounded ${
            severityBadge[finding.severity] || severityBadge.low
          }`}
        >
          {finding.severity}
        </span>
        <code className="text-xs opacity-70">{finding.code}</code>
      </div>
      <p className="font-medium">{finding.message}</p>
      {finding.evidence && (
        <p className="mt-1 text-xs italic opacity-75">
          &ldquo;{finding.evidence}&rdquo;
        </p>
      )}
    </li>
  );
}

export default function QualityCheckPanel({ agreementId }: { agreementId: string }) {
  const [report, setReport] = useState<QualityReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const runCheck = async () => {
    const { token } = (await import("@/contexts/AuthContext")).useAuth();
    setError("");
    setLoading(true);
    try {
      const result = await getQualityCheck(token as string, agreementId);
      setReport(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Quality check failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white shadow rounded-lg p-6">
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-lg font-medium text-gray-900">
          Contract Quality
        </h2>
        <button
          onClick={runCheck}
          disabled={loading}
          className="text-sm text-blue-600 hover:text-blue-800 disabled:opacity-50"
          aria-label="Run contract quality checks"
        >
          {loading ? "Checking…" : report ? "Re-run checks" : "Run checks"}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm mb-3">
          {error}
        </div>
      )}

      {!report && !loading && !error && (
        <p className="text-sm text-gray-500">
          Run the deterministic quality engines: undefined terms, broken
          cross-references, percentage splits, date ordering, and
          party/signatory consistency. Advisory only.
        </p>
      )}

      {report && (
        <>
          <div className="flex items-center gap-3 mb-4">
            {report.has_blockers ? (
              <span className="px-2 py-1 text-xs font-semibold rounded-full bg-red-100 text-red-700">
                {report.counts.high} blocking finding
                {report.counts.high === 1 ? "" : "s"}
              </span>
            ) : (
              <span className="px-2 py-1 text-xs font-semibold rounded-full bg-green-100 text-green-700">
                No blockers
              </span>
            )}
            <span className="text-xs text-gray-500">
              {report.counts.high} high · {report.counts.medium} medium ·{" "}
              {report.counts.low} low
            </span>
          </div>

          {report.findings.length === 0 ? (
            <p className="text-sm text-gray-500">
              All quality engines passed — no findings.
            </p>
          ) : (
            <div className="space-y-4">
              {Object.entries(
                report.findings.reduce<Record<string, QualityFinding[]>>(
                  (acc, f) => {
                    (acc[f.engine] ||= []).push(f);
                    return acc;
                  },
                  {}
                )
              ).map(([engine, findings]) => (
                <div key={engine}>
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">
                    {engineLabels[engine] || engine} ({findings.length})
                  </h3>
                  <ul className="space-y-2">
                    {findings.map((f, i) => (
                      <FindingRow key={`${f.code}-${f.location ?? i}`} finding={f} />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
