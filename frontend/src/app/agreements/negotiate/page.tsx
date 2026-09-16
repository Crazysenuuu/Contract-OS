"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { usePermissions } from "@/hooks/usePermissions";

export const dynamic = "force-dynamic";
import {
  getAgreement,
  listVersions,
  getDiff,
  listChanges,
  acceptChange,
  rejectChange,
} from "@/lib/api";

interface Agreement {
  id: string;
  title: string;
  status: string;
}

interface Version {
  id: string;
  version_number: number;
  status: string;
  content_hash: string;
  created_at: string;
}

interface DiffClause {
  clause_identifier: string;
  old_content: string;
  new_content: string;
  change_type: string;
}

interface Change {
  id: string;
  change_type: string;
  explanation: string | null;
  status: string;
  created_at: string;
}

function NegotiateContent() {
  const searchParams = useSearchParams();
  const agreementId = searchParams.get("id");
  const { token } = useAuth();
  const { canApprove } = usePermissions(agreementId);

  const [agreement, setAgreement] = useState<Agreement | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  const [baseVersion, setBaseVersion] = useState<number>(1);
  const [comparedVersion, setComparedVersion] = useState<number>(2);
  const [diffClauses, setDiffClauses] = useState<DiffClause[]>([]);
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [loading, setLoading] = useState(true);
  const [diffLoading, setDiffLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (token && agreementId) {
      Promise.all([
        getAgreement(token, agreementId),
        listVersions(token, agreementId).catch(() => []),
        listChanges(token, agreementId).catch(() => []),
      ])
        .then(([agr, vers, chngs]) => {
          setAgreement(agr);
          setVersions(vers);
          setChanges(chngs);
          if (vers.length >= 2) {
            setBaseVersion(vers[vers.length - 2].version_number);
            setComparedVersion(vers[vers.length - 1].version_number);
          }
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token, agreementId]);

  useEffect(() => {
    if (token && agreementId && baseVersion && comparedVersion) {
      // All updates in async callbacks — no synchronous setState here.
      getDiff(token, agreementId, baseVersion, comparedVersion)
        .then((data) => {
          setDiffClauses(data.clauses);
          setSummary(data.summary);
        })
        .catch(console.error)
        .finally(() => setDiffLoading(false));
    }
  }, [token, agreementId, baseVersion, comparedVersion]);

  const handleAccept = async (changeId: string) => {
    if (!token || !agreementId) return;
    try {
      await acceptChange(token, agreementId, changeId);
      const chngs = await listChanges(token, agreementId);
      setChanges(chngs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Accept failed");
    }
  };

  const handleReject = async (changeId: string) => {
    if (!token || !agreementId) return;
    try {
      await rejectChange(token, agreementId, changeId);
      const chngs = await listChanges(token, agreementId);
      setChanges(chngs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reject failed");
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

      <h1 className="text-2xl font-bold text-gray-900 mb-6">
        Negotiation: {agreement.title}
      </h1>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Sidebar */}
        <div className="lg:col-span-1 space-y-6">
          {/* Version Selector */}
          <div className="bg-white shadow rounded-lg p-4">
            <h2 className="text-sm font-medium text-gray-900 mb-3">
              Compare Versions
            </h2>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">
                  Base Version
                </label>
                <select
                  value={baseVersion}
                  onChange={(e) => setBaseVersion(Number(e.target.value))}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                >
                  {versions.map((v) => (
                    <option key={v.id} value={v.version_number}>
                      v{v.version_number} ({v.status})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">
                  Compare With
                </label>
                <select
                  value={comparedVersion}
                  onChange={(e) => setComparedVersion(Number(e.target.value))}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                >
                  {versions.map((v) => (
                    <option key={v.id} value={v.version_number}>
                      v{v.version_number} ({v.status})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Diff Summary */}
            {summary && (
              <div className="mt-4 pt-4 border-t border-gray-200">
                <h3 className="text-xs font-medium text-gray-700 mb-2">
                  Summary
                </h3>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="text-gray-500">
                    Total: {summary.total_clauses}
                  </div>
                  <div className="text-green-600">
                    Added: {summary.added}
                  </div>
                  <div className="text-red-600">
                    Removed: {summary.removed}
                  </div>
                  <div className="text-yellow-600">
                    Modified: {summary.modified}
                  </div>
                  <div className="text-gray-400">
                    Unchanged: {summary.unchanged}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Changes List */}
          <div className="bg-white shadow rounded-lg p-4">
            <h2 className="text-sm font-medium text-gray-900 mb-3">
              Change Proposals
            </h2>
            {changes.length === 0 ? (
              <p className="text-xs text-gray-500">No changes proposed yet</p>
            ) : (
              <div className="space-y-3">
                {changes.map((change) => (
                  <div
                    key={change.id}
                    className="p-3 border border-gray-200 rounded-lg"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-gray-700">
                        {change.change_type}
                      </span>
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${
                          change.status === "proposed"
                            ? "bg-yellow-100 text-yellow-800"
                            : change.status === "accepted"
                            ? "bg-green-100 text-green-800"
                            : "bg-red-100 text-red-800"
                        }`}
                      >
                        {change.status}
                      </span>
                    </div>
                    {change.explanation && (
                      <p className="text-xs text-gray-500 mb-2">
                        {change.explanation}
                      </p>
                    )}
                    {change.status === "proposed" && canApprove && (
                      <div className="flex space-x-2">
                        <button
                          onClick={() => handleAccept(change.id)}
                          className="text-xs px-2 py-1 bg-green-600 text-white rounded hover:bg-green-700"
                        >
                          Accept
                        </button>
                        <button
                          onClick={() => handleReject(change.id)}
                          className="text-xs px-2 py-1 bg-red-600 text-white rounded hover:bg-red-700"
                        >
                          Reject
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Main Diff View */}
        <div className="lg:col-span-3">
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">
              Redline View
            </h2>

            {diffLoading ? (
              <div className="text-center py-8 text-gray-500">
                Loading diff...
              </div>
            ) : diffClauses.length === 0 ? (
              <div className="text-center py-8 text-gray-500">
                Select versions to compare
              </div>
            ) : (
              <div className="space-y-4">
                {diffClauses.map((clause) => (
                  <div
                    key={clause.clause_identifier}
                    className={`p-4 rounded-lg border-l-4 ${
                      clause.change_type === "modified"
                        ? "border-yellow-400 bg-yellow-50"
                        : clause.change_type === "added"
                        ? "border-green-400 bg-green-50"
                        : clause.change_type === "removed"
                        ? "border-red-400 bg-red-50"
                        : "border-gray-200 bg-gray-50"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-mono text-gray-600">
                        {clause.clause_identifier}
                      </span>
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          clause.change_type === "modified"
                            ? "bg-yellow-200 text-yellow-800"
                            : clause.change_type === "added"
                            ? "bg-green-200 text-green-800"
                            : clause.change_type === "removed"
                            ? "bg-red-200 text-red-800"
                            : "bg-gray-200 text-gray-800"
                        }`}
                      >
                        {clause.change_type}
                      </span>
                    </div>

                    {clause.change_type === "modified" && (
                      <div className="space-y-2">
                        <div className="text-sm text-red-700 line-through">
                          {clause.old_content}
                        </div>
                        <div className="text-sm text-green-700 font-medium">
                          {clause.new_content}
                        </div>
                      </div>
                    )}

                    {clause.change_type === "added" && (
                      <div className="text-sm text-green-700 font-medium">
                        {clause.new_content}
                      </div>
                    )}

                    {clause.change_type === "removed" && (
                      <div className="text-sm text-red-700 line-through">
                        {clause.old_content}
                      </div>
                    )}

                    {clause.change_type === "unchanged" && (
                      <div className="text-sm text-gray-600">
                        {clause.old_content}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function NegotiatePage() {
  return (
    <Suspense fallback={<div className="text-center py-12 text-gray-500">Loading...</div>}>
      <NegotiateContent />
    </Suspense>
  );
}
