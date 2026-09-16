"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { suggestClauses, assessRisks, compareClauses } from "@/lib/api";

interface ClauseSuggestion {
  clause_type: string;
  name: string;
  text: string;
  explanation: string;
  risk_level: string;
  is_mandatory: boolean;
  alternatives: Array<Record<string, unknown>>;
  confidence: number;
}

interface RiskAssessment {
  category: string;
  severity: string;
  score: number;
  description: string;
  mitigation: string;
}

const riskColors: Record<string, string> = {
  low: "bg-green-100 text-green-800 border-green-300",
  medium: "bg-yellow-100 text-yellow-800 border-yellow-300",
  high: "bg-red-100 text-red-800 border-red-300",
};

const severityColors: Record<string, string> = {
  low: "text-blue-600",
  medium: "text-yellow-600",
  high: "text-orange-600",
  critical: "text-red-600",
};

function ClauseSuggestionsContent() {
  const searchParams = useSearchParams();
  const initialJurisdiction = searchParams.get("jurisdiction") || "LK";
  const { token } = useAuth();

  const [jurisdictionCode, setJurisdictionCode] = useState(initialJurisdiction);
  const [agreementType, setAgreementType] = useState("mutual_nda");
  const [suggestions, setSuggestions] = useState<ClauseSuggestion[]>([]);
  const [loading, setLoading] = useState(false);

  // Risk assessment
  const [assessText, setAssessText] = useState("");
  const [assessType, setAssessType] = useState("confidentiality");
  const [risks, setRisks] = useState<RiskAssessment[]>([]);
  const [assessing, setAssessing] = useState(false);

  // Clause comparison
  const [compareTextA, setCompareTextA] = useState("");
  const [compareTextB, setCompareTextB] = useState("");
  const [compareType, setCompareType] = useState("confidentiality");
  const [comparison, setComparison] = useState<Record<string, unknown> | null>(null);
  const [comparing, setComparing] = useState(false);

  const [activeTab, setActiveTab] = useState<"suggestions" | "assess" | "compare">("suggestions");

  useEffect(() => {
    if (token) {
      // All updates in async callbacks — no synchronous setState here.
      suggestClauses(token, jurisdictionCode, agreementType)
        .then(setSuggestions)
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token, jurisdictionCode, agreementType]);

  const handleAssess = async () => {
    if (!token || !assessText) return;
    setAssessing(true);
    try {
      const result = await assessRisks(token, assessText, assessType, jurisdictionCode);
      setRisks(result);
    } catch (err) {
      console.error(err);
    } finally {
      setAssessing(false);
    }
  };

  const handleCompare = async () => {
    if (!token || !compareTextA || !compareTextB) return;
    setComparing(true);
    try {
      const result = await compareClauses(token, compareType, compareTextA, compareTextB, jurisdictionCode);
      setComparison(result);
    } catch (err) {
      console.error(err);
    } finally {
      setComparing(false);
    }
  };

  return (
    <div>
      <div className="mb-6">
        <Link href="/jurisdictions" className="text-sm text-gray-500 hover:text-gray-700">
          ← Back to Jurisdictions
        </Link>
      </div>

      <h1 className="text-2xl font-bold text-gray-900 mb-6">Clause Suggestions</h1>

      {/* Controls */}
      <div className="bg-white shadow rounded-lg p-6 mb-6">
        <div className="flex flex-wrap gap-4">
          <div>
            <label className="block text-sm text-gray-700 mb-1">Jurisdiction</label>
            <select
              value={jurisdictionCode}
              onChange={(e) => setJurisdictionCode(e.target.value)}
              className="border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              <option value="LK">Sri Lanka (LK)</option>
              <option value="SG">Singapore (SG)</option>
              <option value="US">United States (US)</option>
            </select>
          </div>
          <div>
            <label className="block text-sm text-gray-700 mb-1">Agreement Type</label>
            <select
              value={agreementType}
              onChange={(e) => setAgreementType(e.target.value)}
              className="border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              <option value="mutual_nda">Mutual NDA</option>
              <option value="service_agreement">Service Agreement</option>
              <option value="partnership">Partnership Agreement</option>
            </select>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex space-x-1 mb-6 border-b border-gray-200">
        {[
          { key: "suggestions", label: "📋 Suggested Clauses" },
          { key: "assess", label: "🔍 Risk Assessment" },
          { key: "compare", label: "⚖️ Compare Clauses" },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key as typeof activeTab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.key
                ? "border-blue-500 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Suggestions Tab */}
      {activeTab === "suggestions" && (
        <div className="space-y-4">
          {loading ? (
            <div className="text-center py-8 text-gray-500">Loading suggestions...</div>
          ) : suggestions.length === 0 ? (
            <div className="text-center py-8 text-gray-500">No suggestions available</div>
          ) : (
            suggestions.map((s, i) => (
              <div key={i} className="bg-white shadow rounded-lg p-6">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <h3 className="text-lg font-medium text-gray-900">{s.name}</h3>
                    <div className="flex items-center space-x-2 mt-1">
                      <span className={`px-2 py-0.5 text-xs font-medium rounded ${riskColors[s.risk_level] || "bg-gray-100"}`}>
                        {s.risk_level}
                      </span>
                      {s.is_mandatory && (
                        <span className="px-2 py-0.5 text-xs font-medium bg-red-100 text-red-800 rounded">Mandatory</span>
                      )}
                      <span className="text-xs text-gray-500">
                        Confidence: {Math.round(s.confidence * 100)}%
                      </span>
                    </div>
                  </div>
                </div>
                <p className="text-sm text-gray-600 mb-3">{s.explanation}</p>
                <div className="bg-gray-50 border border-gray-200 rounded p-4">
                  <div className="text-xs text-gray-500 uppercase mb-2">Suggested Text</div>
                  <p className="text-sm text-gray-900 whitespace-pre-wrap">{s.text}</p>
                </div>
                {s.alternatives.length > 0 && (
                  <div className="mt-3">
                    <div className="text-xs text-gray-500 uppercase mb-1">Alternatives</div>
                    {s.alternatives.map((alt, j) => (
                      <div key={j} className="text-sm text-gray-600 bg-blue-50 border border-blue-200 rounded p-2 mt-1">
                        {JSON.stringify(alt)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Risk Assessment Tab */}
      {activeTab === "assess" && (
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">Assess Clause Risks</h2>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm text-gray-700 mb-1">Clause Type</label>
              <select
                value={assessType}
                onChange={(e) => setAssessType(e.target.value)}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                <option value="confidentiality">Confidentiality</option>
                <option value="limitation_of_liability">Limitation of Liability</option>
                <option value="termination">Termination</option>
                <option value="governing_law">Governing Law</option>
              </select>
            </div>
          </div>
          <div className="mb-4">
            <label className="block text-sm text-gray-700 mb-1">Clause Text</label>
            <textarea
              value={assessText}
              onChange={(e) => setAssessText(e.target.value)}
              rows={6}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              placeholder="Paste the clause text here..."
            />
          </div>
          <button
            onClick={handleAssess}
            disabled={assessing || !assessText}
            className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {assessing ? "Assessing..." : "Assess Risks"}
          </button>

          {risks.length > 0 && (
            <div className="mt-6 space-y-3">
              <h3 className="text-sm font-medium text-gray-700">Risk Findings</h3>
              {risks.map((r, i) => (
                <div key={i} className={`border rounded p-3 ${riskColors[r.severity] || "border-gray-300"}`}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-medium text-sm">{r.category}</span>
                    <span className={`text-sm font-bold ${severityColors[r.severity]}`}>Score: {r.score}/100</span>
                  </div>
                  <p className="text-sm">{r.description}</p>
                  <p className="text-sm mt-1 opacity-75">💡 {r.mitigation}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Compare Tab */}
      {activeTab === "compare" && (
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">Compare Two Clauses</h2>
          <div className="mb-4">
            <label className="block text-sm text-gray-700 mb-1">Clause Type</label>
            <select
              value={compareType}
              onChange={(e) => setCompareType(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              <option value="confidentiality">Confidentiality</option>
              <option value="limitation_of_liability">Limitation of Liability</option>
              <option value="termination">Termination</option>
              <option value="governing_law">Governing Law</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm text-gray-700 mb-1">Version A</label>
              <textarea
                value={compareTextA}
                onChange={(e) => setCompareTextA(e.target.value)}
                rows={6}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                placeholder="First clause version..."
              />
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">Version B</label>
              <textarea
                value={compareTextB}
                onChange={(e) => setCompareTextB(e.target.value)}
                rows={6}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                placeholder="Second clause version..."
              />
            </div>
          </div>
          <button
            onClick={handleCompare}
            disabled={comparing || !compareTextA || !compareTextB}
            className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {comparing ? "Comparing..." : "Compare"}
          </button>

          {comparison && (
            <div className="mt-6 grid grid-cols-2 gap-4">
              <div className="border rounded p-4">
                <h4 className="font-medium text-sm mb-2">Version A</h4>
                <div className="text-2xl font-bold text-gray-900">
                  {String((comparison as Record<string, Record<string, unknown>>).version_a?.risk_score)}
                </div>
                <div className="text-sm text-gray-500">Risk Score</div>
              </div>
              <div className="border rounded p-4">
                <h4 className="font-medium text-sm mb-2">Version B</h4>
                <div className="text-2xl font-bold text-gray-900">
                  {String((comparison as Record<string, Record<string, unknown>>).version_b?.risk_score)}
                </div>
                <div className="text-sm text-gray-500">Risk Score</div>
              </div>
              <div className="col-span-2 bg-blue-50 border border-blue-200 rounded p-4 text-center">
                <div className="text-sm text-blue-800">
                  Recommended: <strong>{String((comparison as Record<string, unknown>).recommendation)}</strong>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ClauseSuggestionsPage() {
  return (
    <Suspense fallback={<div className="text-center py-12 text-gray-500">Loading...</div>}>
      <ClauseSuggestionsContent />
    </Suspense>
  );
}
