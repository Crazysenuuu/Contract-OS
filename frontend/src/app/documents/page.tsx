"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { listAgreementDocuments, listAgreements, type RepositoryDocument } from "@/lib/api";

interface Row extends RepositoryDocument {
  agreement_id: string;
  agreement_title: string;
  agreement_status: string;
}

function formatBytes(n: number | null) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const classificationTone: Record<string, string> = {
  public: "bg-gray-100 text-gray-700",
  internal: "bg-blue-100 text-blue-800",
  confidential: "bg-amber-100 text-amber-800",
  highly_confidential: "bg-red-100 text-red-800",
  restricted: "bg-purple-100 text-purple-800",
};

export default function DocumentsPage() {
  const { token } = useAuth();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const [cls, setCls] = useState("all");
  const [onlyImmutable, setOnlyImmutable] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const agreements = await listAgreements(token);
      const nested = await Promise.all(
        agreements.map(async (a) => {
          const res = await listAgreementDocuments(token, a.id).catch(() => ({ documents: [] }));
          return res.documents.map<Row>((d) => ({
            ...d,
            agreement_id: a.id,
            agreement_title: a.title,
            agreement_status: a.status,
          }));
        })
      );
      setRows(
        nested.flat().sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
      );
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    queueMicrotask(() => load());
  }, [load]);

  const types = useMemo(
    () => Array.from(new Set(rows.map((r) => r.document_type).filter(Boolean))) as string[],
    [rows]
  );

  const classifications = useMemo(
    () => Array.from(new Set(rows.map((r) => r.classification).filter(Boolean))) as string[],
    [rows]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter(
      (r) =>
        (type === "all" || r.document_type === type) &&
        (cls === "all" || r.classification === cls) &&
        (!onlyImmutable || r.immutable) &&
        (!q || [r.title, r.filename, r.agreement_title, r.sha256].some((v) => v?.toLowerCase().includes(q)))
    );
  }, [rows, query, type, cls, onlyImmutable]);

  const stats = useMemo(
    () => ({
      total: rows.length,
      immutable: rows.filter((r) => r.immutable).length,
      bytes: rows.reduce((s, r) => s + (r.size_bytes ?? 0), 0),
    }),
    [rows]
  );

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Documents</h1>
          <p className="text-sm text-gray-500">
            Every stored file across your agreements. Executed documents are sealed — the hash shown is the one recorded in
            the audit trail.
          </p>
        </div>
        <Link href="/data-governance" className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">
          Retention &amp; legal holds
        </Link>
      </div>

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        {[
          ["Documents", stats.total.toString()],
          ["Sealed (immutable)", stats.immutable.toString()],
          ["Storage", formatBytes(stats.bytes)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg bg-white p-4 shadow">
            <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
            <div className="mt-1 text-2xl font-semibold text-gray-900">{value}</div>
          </div>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search title, filename, agreement, sha256…"
          className="w-full max-w-md rounded-md border border-gray-300 px-3 py-2 text-sm"
        />
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="all">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select
          value={cls}
          onChange={(e) => setCls(e.target.value)}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="all">All classifications</option>
          {classifications.map((c) => (
            <option key={c} value={c}>
              {c.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={onlyImmutable} onChange={(e) => setOnlyImmutable(e.target.checked)} />
          Sealed only
        </label>
      </div>

      {error && <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <div className="overflow-hidden rounded-lg bg-white shadow">
        {loading ? (
          <div className="py-16 text-center text-gray-500">Loading documents…</div>
        ) : filtered.length === 0 ? (
          <div className="py-16 text-center text-sm text-gray-500">No documents match.</div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3">Document</th>
                <th className="px-4 py-3">Agreement</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Classification</th>
                <th className="px-4 py-3">Size</th>
                <th className="px-4 py-3">Integrity</th>
                <th className="px-4 py-3">Added</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((r) => (
                <tr key={r.id} className="transition hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">{r.title}</div>
                    <div className="text-xs text-gray-500">{r.filename ?? r.media_type ?? "—"}</div>
                  </td>
                  <td className="px-4 py-3">
                    <Link href={`/agreements/${r.agreement_id}`} className="text-blue-600 hover:underline">
                      {r.agreement_title}
                    </Link>
                    <div className="text-xs text-gray-500">{r.agreement_status.replace(/_/g, " ")}</div>
                  </td>
                  <td className="px-4 py-3 text-gray-700">{r.document_type?.replace(/_/g, " ") ?? "—"}</td>
                  <td className="px-4 py-3">
                    {r.classification ? (
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                          classificationTone[r.classification] ?? "bg-gray-100 text-gray-700"
                        }`}
                      >
                        {r.classification.replace(/_/g, " ")}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-700">{formatBytes(r.size_bytes)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span
                        className={`h-2 w-2 rounded-full ${r.immutable ? "bg-green-500" : "bg-gray-300"}`}
                        title={r.immutable ? "Sealed" : "Mutable"}
                      />
                      <code className="font-mono text-[11px] text-gray-500" title={r.sha256 ?? undefined}>
                        {r.sha256 ? `${r.sha256.slice(0, 12)}…` : "—"}
                      </code>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {r.created_at ? new Date(r.created_at).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
