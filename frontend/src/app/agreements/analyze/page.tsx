"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  getAgreement,
  analyzeContract,
  detectRisks,
  listRisks,
  updateRiskStatus,
  listVersions,
  compareVersions,
  listClauses,
  extractClauses,
  addClauseToLibrary,
} from "@/lib/api";

interface Agreement {
  id: string;
  title: string;
  status: string;
  data?: Record<string, unknown>;
}

interface AnalysisCoverage {
  characters_total: number;
  chunks: number;
  chunks_analyzed: number;
  chunks_failed: number;
  truncated: boolean;
  note?: string | null;
}

interface AnalysisResult {
  summary: string;
  key_terms: Record<string, unknown>;
  risks: Array<{
    category: string;
    severity: string;
    finding: string;
    explanation: string | null;
    recommendation: string | null;
    confidence: number;
  }>;
  confidence: number;
  coverage?: AnalysisCoverage | null;
}

interface ExtractedClause {
  id: string;
  title: string;
  category: string;
  risk_level: string | null;
  risk_score: number | null;
  sentiment: string | null;
  tags: string[];
  text?: string;
  text_preview?: string;
}

interface RiskFinding {
  id: string;
  category: string;
  severity: string;
  clause_identifier: string | null;
  finding: string;
  explanation: string | null;
  recommendation: string | null;
  confidence: number;
  reviewer_status: string;
}

interface AgreementVersion {
  id: string;
  version_number: number;
  status: string;
  created_at: string;
}

interface ComparisonResult {
  summary: string;
  changes_detected: number;
  risk_changes: Array<unknown>;
  detailed_changes: Array<unknown>;
  coverage?: AnalysisCoverage | null;
}

function CoverageBanner({ coverage }: { coverage: AnalysisCoverage }) {
  const failed = coverage.chunks_failed > 0;
  const truncated = coverage.truncated;
  if (!failed && !truncated && coverage.chunks <= 1) return null;

  return (
    <div
      className={`mt-3 rounded border p-2 text-xs ${
        truncated
          ? "border-orange-300 bg-orange-50 text-orange-800"
          : failed
            ? "border-yellow-300 bg-yellow-50 text-yellow-800"
            : "border-blue-200 bg-blue-50 text-blue-700"
      }`}
    >
      <span className="font-medium">Analysis coverage:</span>{
      " "}
      {coverage.chunks_analyzed}/{coverage.chunks} section(s) analyzed
      {coverage.chunks_failed > 0 && ` · ${coverage.chunks_failed} failed`}
      {" · "}
      {coverage.characters_total.toLocaleString()} characters read
      {truncated && " · capped by AI_MAX_CHUNKS_PER_ANALYSIS — some sections were skipped"}
      {coverage.note && <div className="mt-1 italic">{coverage.note}</div>}
    </div>
  );
}

const severityColors: Record<string, string> = {
  critical: "bg-red-100 text-red-800 border-red-300",
  high: "bg-orange-100 text-orange-800 border-orange-300",
  medium: "bg-yellow-100 text-yellow-800 border-yellow-300",
  low: "bg-blue-100 text-blue-800 border-blue-300",
  info: "bg-gray-100 text-gray-800 border-gray-300",
};

const categoryLabels: Record<string, string> = {
  financial: "Financial",
  liability: "Liability",
  ip: "Intellectual Property",
  privacy: "Privacy",
  security: "Security",
  termination: "Termination",
  renewal: "Renewal",
  operational: "Operational",
  regulatory: "Regulatory",
  jurisdiction: "Jurisdiction",
  payment: "Payment",
  confidentiality: "Confidentiality",
};

function AnalysisContent() {
  const searchParams = useSearchParams();
  const agreementId = searchParams.get("id");
  const { token } = useAuth();

  const [agreement, setAgreement] = useState<Agreement | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [risks, setRisks] = useState<RiskFinding[]>([]);
  const [versions, setVersions] = useState<AgreementVersion[]>([]);
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [clauses, setClauses] = useState<ExtractedClause[]>([]);

  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState("");

  // Comparison form
  const [baseVersion, setBaseVersion] = useState<number>(1);
  const [comparedVersion, setComparedVersion] = useState<number>(2);
  const [savingClauseId, setSavingClauseId] = useState<string>("");
  const [savedClauseIds, setSavedClauseIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (token && agreementId) {
      Promise.all([
        getAgreement(token, agreementId),
        listRisks(token, agreementId).catch(() => []),
        listVersions(token, agreementId).catch(() => []),
        listClauses(token, agreementId).catch(() => []),
      ])
        .then(([agr, riskData, vers, clauseData]) => {
          setAgreement(agr);
          setRisks(riskData);
          setVersions(vers);
          setClauses(clauseData);
          if (vers.length >= 2) {
            setBaseVersion(1);
            setComparedVersion(vers.length);
          }
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token, agreementId]);

  const handleAnalyze = async () => {
    if (!token || !agreementId) return;

    setAnalyzing(true);
    setError("");
    try {
      const result = await analyzeContract(token, agreementId);
      setAnalysis(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleDetectRisks = async () => {
    if (!token || !agreementId) return;

    setDetecting(true);
    setError("");
    try {
      const result = await detectRisks(token, agreementId);
      setRisks(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Risk detection failed");
    } finally {
      setDetecting(false);
    }
  };

  const handleCompare = async () => {
    if (!token || !agreementId) return;

    setComparing(true);
    setError("");
    try {
      const result = await compareVersions(
        token,
        agreementId,
        baseVersion,
        comparedVersion
      );
      setComparison(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Comparison failed");
    } finally {
      setComparing(false);
    }
  };

  const handleRiskStatus = async (riskId: string, status: string) => {
    if (!token || !agreementId) return;

    try {
      await updateRiskStatus(token, agreementId, riskId, {
        reviewer_status: status,
      });
      setRisks(
        risks.map((r) =>
          r.id === riskId ? { ...r, reviewer_status: status } : r
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleExtractClauses = async () => {
    if (!token || !agreementId) return;
    setAnalyzing(true);
    setError("");
    try {
      const result = await extractClauses(token, {
        agreement_id: agreementId,
        text: agreement?.data ? JSON.stringify(agreement.data) : "",
      });
      setClauses(result.clauses);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Clause extraction failed");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSaveToLibrary = async (clause: ExtractedClause) => {
    if (!token) return;
    setSavingClauseId(clause.id);
    try {
      await addClauseToLibrary(token, {
        clause_id: clause.id,
        title: clause.title,
        description: clause.text_preview ?? clause.text?.slice(0, 200),
      });
      setSavedClauseIds((prev) => new Set(prev).add(clause.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save to library failed");
    } finally {
      setSavingClauseId("");
    }
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  if (!agreement) {
    return (
      <div className="text-center py-12 text-gray-500">
        Agreement not found
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          href={`/agreements/${agreementId}`}
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          ← Back to Agreement
        </Link>
      </div>

      <div className="flex justify-between items-start mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">AI Analysis</h1>
          <p className="text-sm text-gray-500 mt-1">{agreement.title}</p>
        </div>
        <div className="flex space-x-3">
          <button
            onClick={handleAnalyze}
            disabled={analyzing}
            className="px-4 py-2 text-sm font-medium text-white bg-purple-600 rounded-md hover:bg-purple-700 disabled:opacity-50"
          >
            {analyzing ? "Analyzing..." : "Run Full Analysis"}
          </button>
          <button
            onClick={handleDetectRisks}
            disabled={detecting}
            className="px-4 py-2 text-sm font-medium text-white bg-orange-600 rounded-md hover:bg-orange-700 disabled:opacity-50"
          >
            {detecting ? "Detecting..." : "Detect Risks"}
          </button>
          <button
            onClick={handleExtractClauses}
            disabled={analyzing}
            className="px-4 py-2 text-sm font-medium text-white bg-teal-600 rounded-md hover:bg-teal-700 disabled:opacity-50"
          >
            {analyzing ? "Extracting..." : "Extract Clauses"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Analysis Summary */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Contract Summary
          </h2>

          {analysis ? (
            <div>
              <p className="text-sm text-gray-700 whitespace-pre-wrap">
                {analysis.summary}
              </p>

              {Object.keys(analysis.key_terms).length > 0 && (
                <div className="mt-4">
                  <h3 className="text-sm font-medium text-gray-700 mb-2">
                    Key Terms
                  </h3>
                  <div className="bg-gray-50 rounded p-3">
                    {Object.entries(analysis.key_terms).map(([key, value]) => (
                      <div key={key} className="flex justify-between py-1 text-sm">
                        <span className="text-gray-500">{key}:</span>
                        <span className="text-gray-900">
                          {String(value)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="mt-4 text-sm text-gray-500">
                Confidence: {Math.round(analysis.confidence * 100)}%
              </div>
              {analysis.coverage && (
                <CoverageBanner coverage={analysis.coverage} />
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              Click &quot;Run Full Analysis&quot; to get an AI-powered summary of this
              contract.
            </p>
          )}
        </div>

        {/* Risk Summary */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Risk Overview
          </h2>

          {risks.length > 0 ? (
            <div>
              <div className="grid grid-cols-5 gap-2 mb-4">
                {["critical", "high", "medium", "low", "info"].map(
                  (severity) => (
                    <div key={severity} className="text-center">
                      <div
                        className={`text-2xl font-bold ${
                          severity === "critical"
                            ? "text-red-600"
                            : severity === "high"
                            ? "text-orange-600"
                            : severity === "medium"
                            ? "text-yellow-600"
                            : severity === "low"
                            ? "text-blue-600"
                            : "text-gray-600"
                        }`}
                      >
                        {risks.filter((r) => r.severity === severity).length}
                      </div>
                      <div className="text-xs text-gray-500 capitalize">
                        {severity}
                      </div>
                    </div>
                  )
                )}
              </div>

              <div className="space-y-2">
                {risks.slice(0, 5).map((risk) => (
                  <div
                    key={risk.id}
                    className={`p-2 rounded border text-sm ${
                      severityColors[risk.severity] || severityColors.info
                    }`}
                  >
                    <div className="font-medium">{risk.finding}</div>
                    <div className="text-xs opacity-75">
                      {categoryLabels[risk.category] || risk.category}
                    </div>
                  </div>
                ))}
              </div>

              {risks.length > 5 && (
                <p className="text-sm text-gray-500 mt-2">
                  + {risks.length - 5} more risks
                </p>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              No risks detected yet. Click &quot;Detect Risks&quot; to analyze.
            </p>
          )}
        </div>
      </div>

      {/* Extracted Clauses */}
      {clauses.length > 0 && (
        <div className="mt-6 bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Extracted Clauses ({clauses.length})
          </h2>
          <div className="space-y-3">
            {clauses.map((clause) => (
              <div
                key={clause.id}
                className="border border-gray-200 rounded-lg p-4 flex items-start justify-between gap-4"
              >
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium text-gray-900">{clause.title}</span>
                    <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                      {clause.category}
                    </span>
                    {clause.risk_level && (
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${
                          clause.risk_level === "high"
                            ? "bg-red-100 text-red-700"
                            : clause.risk_level === "medium"
                            ? "bg-yellow-100 text-yellow-700"
                            : "bg-green-100 text-green-700"
                        }`}
                      >
                        {clause.risk_level} risk
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 line-clamp-2">
                    {clause.text_preview ?? clause.text?.slice(0, 200)}
                  </p>
                </div>
                <button
                  onClick={() => handleSaveToLibrary(clause)}
                  disabled={savingClauseId === clause.id || savedClauseIds.has(clause.id)}
                  className={`text-xs px-3 py-1.5 rounded whitespace-nowrap ${
                    savedClauseIds.has(clause.id)
                      ? "bg-green-100 text-green-700 cursor-default"
                      : "border border-gray-300 text-gray-700 hover:bg-gray-50"
                  } disabled:opacity-50`}
                >
                  {savedClauseIds.has(clause.id)
                    ? "✓ In library"
                    : savingClauseId === clause.id
                    ? "Saving..."
                    : "Save to library"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Detailed Risks */}
      {risks.length > 0 && (
        <div className="mt-6 bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            All Risk Findings
          </h2>

          <div className="space-y-4">
            {risks.map((risk) => (
              <div
                key={risk.id}
                className="border border-gray-200 rounded-lg p-4"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center space-x-2 mb-2">
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded ${
                          severityColors[risk.severity] || severityColors.info
                        }`}
                      >
                        {risk.severity}
                      </span>
                      <span className="text-xs text-gray-500">
                        {categoryLabels[risk.category] || risk.category}
                      </span>
                      {risk.clause_identifier && (
                        <span className="text-xs text-gray-400">
                          {risk.clause_identifier}
                        </span>
                      )}
                    </div>

                    <p className="text-sm text-gray-900 font-medium">
                      {risk.finding}
                    </p>

                    {risk.explanation && (
                      <p className="text-sm text-gray-600 mt-1">
                        {risk.explanation}
                      </p>
                    )}

                    {risk.recommendation && (
                      <p className="text-sm text-blue-600 mt-1">
                        💡 {risk.recommendation}
                      </p>
                    )}

                    <div className="text-xs text-gray-400 mt-2">
                      Confidence: {Math.round(risk.confidence * 100)}%
                    </div>
                  </div>

                  <div className="flex space-x-2 ml-4">
                    {risk.reviewer_status === "pending" && (
                      <>
                        <button
                          onClick={() => handleRiskStatus(risk.id, "accepted")}
                          className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded hover:bg-green-200"
                        >
                          Accept
                        </button>
                        <button
                          onClick={() => handleRiskStatus(risk.id, "rejected")}
                          className="px-2 py-1 text-xs bg-red-100 text-red-700 rounded hover:bg-red-200"
                        >
                          Reject
                        </button>
                      </>
                    )}
                    {risk.reviewer_status !== "pending" && (
                      <span
                        className={`px-2 py-1 text-xs rounded ${
                          risk.reviewer_status === "accepted"
                            ? "bg-green-100 text-green-700"
                            : risk.reviewer_status === "rejected"
                            ? "bg-red-100 text-red-700"
                            : "bg-gray-100 text-gray-700"
                        }`}
                      >
                        {risk.reviewer_status}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Version Comparison */}
      {versions.length >= 2 && (
        <div className="mt-6 bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Version Comparison
          </h2>

          <div className="flex items-end space-x-4 mb-4">
            <div>
              <label className="block text-sm text-gray-700 mb-1">
                Base Version
              </label>
              <select
                value={baseVersion}
                onChange={(e) => setBaseVersion(Number(e.target.value))}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                {versions.map((v) => (
                  <option key={v.id} value={v.version_number}>
                    v{v.version_number} ({v.status})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm text-gray-700 mb-1">
                Compare With
              </label>
              <select
                value={comparedVersion}
                onChange={(e) => setComparedVersion(Number(e.target.value))}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                {versions.map((v) => (
                  <option key={v.id} value={v.version_number}>
                    v{v.version_number} ({v.status})
                  </option>
                ))}
              </select>
            </div>

            <button
              onClick={handleCompare}
              disabled={comparing || baseVersion === comparedVersion}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {comparing ? "Comparing..." : "Compare"}
            </button>
          </div>

          {comparison && (
            <div className="mt-4">
              <p className="text-sm text-gray-700 mb-4">{comparison.summary}</p>

              {comparison.coverage && (
                <CoverageBanner coverage={comparison.coverage} />
              )}

              <div className="grid grid-cols-3 gap-4 mb-4">
                <div className="text-center p-3 bg-gray-50 rounded">
                  <div className="text-2xl font-bold text-gray-900">
                    {comparison.changes_detected}
                  </div>
                  <div className="text-xs text-gray-500">Changes Detected</div>
                </div>
                <div className="text-center p-3 bg-gray-50 rounded">
                  <div className="text-2xl font-bold text-gray-900">
                    {comparison.risk_changes.length}
                  </div>
                  <div className="text-xs text-gray-500">Risk Changes</div>
                </div>
                <div className="text-center p-3 bg-gray-50 rounded">
                  <div className="text-2xl font-bold text-gray-900">
                    {comparison.detailed_changes.length}
                  </div>
                  <div className="text-xs text-gray-500">Detailed Changes</div>
                </div>
              </div>

              {comparison.detailed_changes.length > 0 && (
                <div className="space-y-3">
                  <h3 className="text-sm font-medium text-gray-700">
                    Detailed Changes
                  </h3>
                  {comparison.detailed_changes.map((change, i) => (
                    <div
                      key={i}
                      className="border border-gray-200 rounded p-3 text-sm"
                    >
                      <pre className="whitespace-pre-wrap text-gray-700">
                        {JSON.stringify(change, null, 2)}
                      </pre>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense fallback={<div className="text-center py-12 text-gray-500">Loading...</div>}>
      <AnalysisContent />
    </Suspense>
  );
}
