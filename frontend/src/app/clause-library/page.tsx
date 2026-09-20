"use client";

import { Suspense, useCallback } from "react";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useRequireAuth } from "@/hooks/useRequireAuth";
import {
  listClauseLibrary,
  getClauseStats,
} from "@/lib/api";

interface LibraryClause {
  id: string;
  title: string;
  category: string;
  risk_level: string | null;
  risk_score: number | null;
  usage_count: number;
  tags: string[];
  text_preview: string;
}

interface ClauseStats {
  total_clauses_extracted: number;
  library_entries: number;
  risk_distribution: Record<string, number>;
  category_distribution: Record<string, number>;
}

function ClauseLibraryContent() {
  const router = useRouter();
  const { token } = useAuth();
  useRequireAuth();
  const [clauses, setClauses] = useState<LibraryClause[]>([]);
  const [stats, setStats] = useState<ClauseStats | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [expandedClause, setExpandedClause] = useState<string | null>(null);

  const categories = [
    "confidentiality", "indemnification", "liability", "termination",
    "governing_law", "dispute_resolution", "ip_ownership", "ip_license",
    "non_compete", "non_solicitation", "representations", "warranties",
    "force_majeure", "severability", "entire_agreement", "payment",
    "insurance", "data_protection", "audit_rights", "other"
  ];

  // Declared before the effect that calls it; no leading setLoading(true)
  // so the mount effect never triggers a synchronous setState cascade.
  const loadData = useCallback(async () => {
    if (!token) return;
    try {
      const [clauseData, statsData] = await Promise.all([
        listClauseLibrary(token, selectedCategory || undefined),
        getClauseStats(token),
      ]);
      setClauses(clauseData);
      setStats(statsData);
    } catch (err) {
      console.error("Failed to load clause library:", err);
    } finally {
      setLoading(false);
    }
  }, [token, selectedCategory]);

  useEffect(() => {
    if (!token) return;
    // Defer so the effect body never triggers a synchronous setState cascade
    // (loadData ends with setLoading in a finally block).
    queueMicrotask(() => loadData());
  }, [token, selectedCategory, loadData, router]);

  const getRiskColor = (level: string | null) => {
    switch (level) {
      case "critical": return "text-red-700 bg-red-100";
      case "high": return "text-orange-700 bg-orange-100";
      case "medium": return "text-yellow-700 bg-yellow-100";
      case "low": return "text-green-700 bg-green-100";
      default: return "text-gray-700 bg-gray-100";
    }
  };

  const formatCategory = (cat: string) => {
    return cat.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
  };

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">📚 Clause Library</h1>
        <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
          ← Back to Dashboard
        </button>
      </div>

      {/* Stats Overview */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold">{stats.total_clauses_extracted}</div>
            <div className="text-sm text-gray-500">Clauses Extracted</div>
          </div>
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold">{stats.library_entries}</div>
            <div className="text-sm text-gray-500">Library Entries</div>
          </div>
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold text-red-600">
              {(stats.risk_distribution.high || 0) + (stats.risk_distribution.critical || 0)}
            </div>
            <div className="text-sm text-gray-500">High/Critical Risks</div>
          </div>
          <div className="bg-white rounded-lg border p-4">
            <div className="text-2xl font-bold text-green-600">{stats.risk_distribution.low || 0}</div>
            <div className="text-sm text-gray-500">Low Risk Clauses</div>
          </div>
        </div>
      )}

      {/* Category Filter */}
      <div className="bg-white rounded-lg border p-4 mb-6">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">Filter by category:</span>
          <button
            onClick={() => setSelectedCategory("")}
            className={`px-3 py-1 rounded text-sm ${
              !selectedCategory ? "bg-blue-600 text-white" : "bg-gray-100 hover:bg-gray-200"
            }`}
          >
            All
          </button>
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat)}
              className={`px-3 py-1 rounded text-sm ${
                selectedCategory === cat ? "bg-blue-600 text-white" : "bg-gray-100 hover:bg-gray-200"
              }`}
            >
              {formatCategory(cat)}
            </button>
          ))}
        </div>
      </div>

      {/* Risk Distribution */}
      {stats && Object.keys(stats.risk_distribution).length > 0 && (
        <div className="bg-white rounded-lg border p-4 mb-6">
          <h3 className="text-sm font-medium mb-3">Risk Distribution</h3>
          <div className="flex items-center gap-2">
            {["low", "medium", "high", "critical"].map((level) => {
              const count = stats.risk_distribution[level] || 0;
              const total = Object.values(stats.risk_distribution).reduce((a, b) => a + b, 0);
              const pct = total > 0 ? (count / total) * 100 : 0;
              return (
                <div key={level} className="flex-1">
                  <div className="flex justify-between text-xs mb-1">
                    <span className="capitalize">{level}</span>
                    <span>{count}</span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded">
                    <div
                      className={`h-full rounded ${
                        level === "critical" ? "bg-red-500" :
                        level === "high" ? "bg-orange-500" :
                        level === "medium" ? "bg-yellow-500" : "bg-green-500"
                      }`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Clauses List */}
      <div className="bg-white rounded-lg border">
        <div className="p-4 border-b">
          <h2 className="text-lg font-semibold">
            Clause Library ({clauses.length} entries)
          </h2>
        </div>

        {loading ? (
          <div className="p-8 text-center text-gray-500">Loading...</div>
        ) : clauses.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No clauses in library yet. Extract clauses from agreements to build your library.
          </div>
        ) : (
          <div className="divide-y">
            {clauses.map((clause) => (
              <div key={clause.id} className="p-4 hover:bg-gray-50">
                <div
                  className="flex items-start justify-between cursor-pointer"
                  onClick={() => setExpandedClause(expandedClause === clause.id ? null : clause.id)}
                >
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-medium">{clause.title}</h3>
                      <span className="text-xs px-2 py-0.5 rounded bg-gray-100">
                        {formatCategory(clause.category)}
                      </span>
                      {clause.risk_level && (
                        <span className={`text-xs px-2 py-0.5 rounded ${getRiskColor(clause.risk_level)}`}>
                          {clause.risk_level}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-600 line-clamp-2">{clause.text_preview}</p>
                    <div className="flex items-center gap-2 mt-2">
                      {clause.tags.slice(0, 3).map((tag) => (
                        <span key={tag} className="text-xs px-2 py-0.5 rounded bg-blue-50 text-blue-700">
                          {tag}
                        </span>
                      ))}
                      {clause.tags.length > 3 && (
                        <span className="text-xs text-gray-400">+{clause.tags.length - 3} more</span>
                      )}
                    </div>
                  </div>
                  <div className="text-right ml-4">
                    <div className="text-sm text-gray-500">Used {clause.usage_count}x</div>
                    {clause.risk_score !== null && (
                      <div className="text-sm mt-1">
                        Risk: <span className="font-mono">{(clause.risk_score * 100).toFixed(0)}%</span>
                      </div>
                    )}
                  </div>
                </div>

                {expandedClause === clause.id && (
                  <div className="mt-4 p-4 bg-gray-50 rounded">
                    <pre className="text-sm text-gray-700 whitespace-pre-wrap">{clause.text_preview}</pre>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ClauseLibraryPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-64">Loading...</div>}>
      <ClauseLibraryContent />
    </Suspense>
  );
}
