"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listVersions,
  getAgreement,
  getAgreementVersion,
  createAgreementVersion,
  compareAgreementVersions,
  restoreAgreementVersion,
  type AgreementVersionSummary,
  type AgreementVersionDetail,
} from "@/lib/api";

export const dynamic = "force-dynamic";

type CompareResult = Awaited<ReturnType<typeof compareAgreementVersions>>;

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  current: "bg-blue-100 text-blue-800",
  proposed: "bg-yellow-100 text-yellow-800",
  superseded: "bg-gray-100 text-gray-500",
  locked: "bg-green-100 text-green-800",
};

function VersionHistory() {
  const { id } = useParams<{ id: string }>();
  const { token } = useAuth();

  const [title, setTitle] = useState("");
  const [versions, setVersions] = useState<AgreementVersionSummary[]>([]);
  const [selected, setSelected] = useState<AgreementVersionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const [note, setNote] = useState("");
  const [fromVersion, setFromVersion] = useState<number | "">("");
  const [toVersion, setToVersion] = useState<number | "">("");
  const [compare, setCompare] = useState<CompareResult | null>(null);

  const applyVersions = useCallback(
    (agreement: { title: string }, list: AgreementVersionSummary[]) => {
      setTitle(agreement.title);
      setVersions(list);
      if (list.length >= 2) {
        const prior = list[list.length - 2].version_number;
        const latest = list[list.length - 1].version_number;
        setFromVersion((prev) => (prev === "" ? prior : prev));
        setToVersion((prev) => (prev === "" ? latest : prev));
      }
    },
    []
  );

  useEffect(() => {
    if (!token || !id) return;
    Promise.all([getAgreement(token, id), listVersions(token, id)])
      .then(([agreement, list]) => applyVersions(agreement, list))
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Failed to load versions")
      )
      .finally(() => setLoading(false));
  }, [token, id, applyVersions]);

  const load = useCallback(async () => {
    if (!token || !id) return;
    try {
      const [agreement, list] = await Promise.all([
        getAgreement(token, id),
        listVersions(token, id),
      ]);
      applyVersions(agreement, list);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load versions");
    } finally {
      setLoading(false);
    }
  }, [token, id, applyVersions]);

  const viewVersion = async (versionNumber: number) => {
    if (!token || !id) return;
    try {
      setCompare(null);
      setSelected(await getAgreementVersion(token, id, versionNumber));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load version");
    }
  };

  const saveVersion = async () => {
    if (!token || !id) return;
    setBusy(true);
    setError("");
    try {
      await createAgreementVersion(token, id, { note: note || undefined });
      setNote("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save version");
    } finally {
      setBusy(false);
    }
  };

  const runCompare = async () => {
    if (!token || !id || fromVersion === "" || toVersion === "") return;
    setBusy(true);
    setError("");
    try {
      setSelected(null);
      setCompare(
        await compareAgreementVersions(token, id, fromVersion, toVersion)
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to compare versions");
    } finally {
      setBusy(false);
    }
  };

  const restore = async (versionNumber: number) => {
    if (!token || !id) return;
    if (
      !window.confirm(
        `Restore version ${versionNumber}? This appends a new version and resets the working draft to those answers. Existing versions are never modified.`
      )
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await restoreAgreementVersion(token, id, versionNumber);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to restore version");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <div className="p-8 text-sm text-gray-500">Loading versions...</div>;
  }

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Link
            href={`/agreements/${id}`}
            className="text-sm text-blue-600 hover:text-blue-800"
          >
            ← Back to agreement
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 mt-1">
            Version history
          </h1>
          {title && <p className="text-sm text-gray-500">{title}</p>}
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label className="block text-xs text-gray-500 mb-1">
              Version note
            </label>
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Sent to counterparty"
              className="px-3 py-2 border border-gray-300 rounded-md text-sm"
            />
          </div>
          <button
            onClick={saveVersion}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            Save version
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Version list */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            {versions.length} version{versions.length === 1 ? "" : "s"}
          </h2>
          {versions.length === 0 ? (
            <p className="text-sm text-gray-500">No versions yet</p>
          ) : (
            <div className="space-y-2">
              {versions.map((v) => (
                <div
                  key={v.id}
                  className="flex items-center justify-between p-3 border border-gray-200 rounded-lg"
                >
                  <button
                    onClick={() => viewVersion(v.version_number)}
                    className="text-left flex-1"
                  >
                    <div className="font-medium text-sm text-gray-900">
                      Version {v.version_number}
                      {v.note && (
                        <span className="ml-2 font-normal text-gray-500">
                          {v.note}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-gray-500">
                      {new Date(v.created_at).toLocaleString()}
                      <span className="ml-2 font-mono text-gray-400">
                        {v.content_hash.slice(0, 8)}
                      </span>
                    </div>
                  </button>
                  <div className="flex items-center gap-2 ml-3">
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full ${
                        statusColors[v.status] || "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {v.status}
                    </span>
                    <button
                      onClick={() => restore(v.version_number)}
                      disabled={busy || v.status === "locked"}
                      className="text-xs text-blue-600 hover:text-blue-800 disabled:opacity-40"
                    >
                      Restore
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Compare controls */}
          <div className="mt-6 pt-4 border-t border-gray-200">
            <h3 className="text-sm font-medium text-gray-900 mb-2">
              Compare versions
            </h3>
            <div className="flex items-center gap-2">
              <select
                value={fromVersion}
                onChange={(e) =>
                  setFromVersion(e.target.value ? Number(e.target.value) : "")
                }
                className="px-2 py-1.5 border border-gray-300 rounded-md text-sm"
              >
                <option value="">From...</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.version_number}>
                    v{v.version_number}
                  </option>
                ))}
              </select>
              <span className="text-gray-400">→</span>
              <select
                value={toVersion}
                onChange={(e) =>
                  setToVersion(e.target.value ? Number(e.target.value) : "")
                }
                className="px-2 py-1.5 border border-gray-300 rounded-md text-sm"
              >
                <option value="">To...</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.version_number}>
                    v{v.version_number}
                  </option>
                ))}
              </select>
              <button
                onClick={runCompare}
                disabled={busy || fromVersion === "" || toVersion === ""}
                className="px-3 py-1.5 text-sm font-medium text-white bg-gray-800 rounded-md hover:bg-gray-900 disabled:opacity-50"
              >
                Compare
              </button>
            </div>
          </div>
        </div>

        {/* Detail / diff panel */}
        <div className="bg-white shadow rounded-lg p-6">
          {compare ? (
            <div>
              <h2 className="text-lg font-medium text-gray-900 mb-4">
                v{compare.from_version} → v{compare.to_version}
              </h2>

              <h3 className="text-sm font-medium text-gray-700 mb-2">
                Answers changed
              </h3>
              {compare.data_diff.added.length === 0 &&
              compare.data_diff.removed.length === 0 &&
              compare.data_diff.changed.length === 0 ? (
                <p className="text-sm text-gray-500 mb-4">
                  No answer changes.
                </p>
              ) : (
                <ul className="text-sm space-y-1 mb-4">
                  {compare.data_diff.added.map((d) => (
                    <li key={`a-${d.key}`} className="text-green-700">
                      + {d.key}: {JSON.stringify(d.new)}
                    </li>
                  ))}
                  {compare.data_diff.removed.map((d) => (
                    <li key={`r-${d.key}`} className="text-red-700">
                      − {d.key}: {JSON.stringify(d.old)}
                    </li>
                  ))}
                  {compare.data_diff.changed.map((d) => (
                    <li key={`c-${d.key}`} className="text-amber-700">
                      ~ {d.key}: {JSON.stringify(d.old)} →{" "}
                      {JSON.stringify(d.new)}
                    </li>
                  ))}
                </ul>
              )}

              <h3 className="text-sm font-medium text-gray-700 mb-2">
                Content diff
              </h3>
              {compare.content_diff ? (
                <pre className="text-xs bg-gray-50 border border-gray-200 rounded p-3 overflow-auto max-h-96 whitespace-pre-wrap">
                  {compare.content_diff.split("\n").map((line, i) => (
                    <div
                      key={i}
                      className={
                        line.startsWith("+") && !line.startsWith("+++")
                          ? "text-green-700"
                          : line.startsWith("-") && !line.startsWith("---")
                          ? "text-red-700"
                          : line.startsWith("@@")
                          ? "text-blue-600"
                          : "text-gray-700"
                      }
                    >
                      {line || " "}
                    </div>
                  ))}
                </pre>
              ) : (
                <p className="text-sm text-gray-500">
                  No rendered content to diff (draft versions carry answers
                  only until rendered).
                </p>
              )}
            </div>
          ) : selected ? (
            <div>
              <h2 className="text-lg font-medium text-gray-900 mb-1">
                Version {selected.version_number}
              </h2>
              <p className="text-xs text-gray-500 mb-4">
                {new Date(selected.created_at).toLocaleString()} ·{" "}
                {selected.status}
                {selected.note ? ` · ${selected.note}` : ""}
              </p>

              <h3 className="text-sm font-medium text-gray-700 mb-2">
                Answers snapshot
              </h3>
              {selected.data && Object.keys(selected.data).length > 0 ? (
                <dl className="text-sm space-y-1 mb-4">
                  {Object.entries(selected.data).map(([k, val]) => (
                    <div key={k} className="flex justify-between gap-4">
                      <dt className="text-gray-500">{k}</dt>
                      <dd className="text-gray-900 text-right">
                        {typeof val === "object"
                          ? JSON.stringify(val)
                          : String(val)}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-sm text-gray-500 mb-4">
                  No answers recorded for this version.
                </p>
              )}

              <h3 className="text-sm font-medium text-gray-700 mb-2">
                Rendered content
              </h3>
              {selected.content ? (
                <pre className="text-xs bg-gray-50 border border-gray-200 rounded p-3 overflow-auto max-h-72 whitespace-pre-wrap">
                  {selected.content}
                </pre>
              ) : (
                <p className="text-sm text-gray-500">
                  Not rendered yet. Render the agreement to capture text.
                </p>
              )}

              <div className="mt-4">
                <button
                  onClick={() => restore(selected.version_number)}
                  disabled={busy || selected.status === "locked"}
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
                >
                  Restore this version
                </button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              Select a version to view its answers, or compare two versions.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function VersionHistoryPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-gray-500">Loading...</div>}>
      <VersionHistory />
    </Suspense>
  );
}
