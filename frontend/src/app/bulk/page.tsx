"use client";

import { Suspense, useCallback } from "react";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useRequireAuth } from "@/hooks/useRequireAuth";
import {
  bulkImport,
  bulkAction,
  listBulkJobs,
  bulkExport,
} from "@/lib/api";

interface BulkJob {
  id: string;
  job_type: string;
  status: string;
  total_items: number;
  successful_items: number;
  failed_items: number;
  created_at: string | null;
}

function BulkPageContent() {
  const router = useRouter();
  const { token } = useAuth();
  useRequireAuth();
  const [activeTab, setActiveTab] = useState<"import" | "export" | "actions" | "history">("import");
  const [jobs, setJobs] = useState<BulkJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [csvContent, setCsvContent] = useState("");
  const [importType, setImportType] = useState("agreements");
  const [selectedAction, setSelectedAction] = useState("status_change");
  const [actionOptions, setActionOptions] = useState<Record<string, string>>({});

  // Declared before the effect that calls it (hoisting breaks the compiler).
  const loadJobs = useCallback(async () => {
    if (!token) return;
    try {
      const data = await listBulkJobs(token);
      setJobs(data);
    } catch (err) {
      console.error("Failed to load jobs:", err);
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    queueMicrotask(() => loadJobs());
  }, [token, loadJobs]);

  const handleImport = async () => {
    if (!token || !csvContent.trim()) return;
    setLoading(true);
    try {
      const result = await bulkImport(token, { csv_content: csvContent, import_type: importType });
      alert(`Import started! Job ID: ${result.job_id}\nTotal: ${result.total_items}, Success: ${result.successful_items}, Failed: ${result.failed_items}`);
      setCsvContent("");
      loadJobs();
    } catch (err) {
      alert(`Import failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setLoading(false);
    }
  };

  const handleBulkAction = async () => {
    if (!token) return;
    setLoading(true);
    try {
      const result = await bulkAction(token, {
        action: selectedAction,
        options: actionOptions,
      });
      alert(`Action completed! Processed: ${result.total_items}, Success: ${result.successful_items}`);
      loadJobs();
    } catch (err) {
      alert(`Action failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async (format: string) => {
    if (!token) return;
    setLoading(true);
    try {
      const result = await bulkExport(token, { export_type: "agreements", format });
      alert(`Export started! Job ID: ${result.job_id}\nRows: ${result.row_count}`);
      loadJobs();
    } catch (err) {
      alert(`Export failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "completed": return "text-green-600 bg-green-50";
      case "processing": return "text-blue-600 bg-blue-50";
      case "failed": return "text-red-600 bg-red-50";
      case "partial": return "text-yellow-600 bg-yellow-50";
      default: return "text-gray-600 bg-gray-50";
    }
  };

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Bulk Operations</h1>
        <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
          ← Back to Dashboard
        </button>
      </div>

      {/* Tabs */}
      <div className="flex space-x-1 mb-6 border-b">
        {(["import", "export", "actions", "history"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium capitalize ${
              activeTab === tab
                ? "border-b-2 border-blue-600 text-blue-600"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {tab === "import" ? "📥 Import" : tab === "export" ? "📤 Export" : tab === "actions" ? "⚡ Mass Actions" : "📋 History"}
          </button>
        ))}
      </div>

      {/* Import Tab */}
      {activeTab === "import" && (
        <div className="bg-white rounded-lg border p-6">
          <h2 className="text-lg font-semibold mb-4">Import from CSV</h2>

          <div className="mb-4">
            <label className="block text-sm font-medium mb-2">Import Type</label>
            <select
              value={importType}
              onChange={(e) => setImportType(e.target.value)}
              className="w-full border rounded px-3 py-2"
            >
              <option value="agreements">Agreements</option>
              <option value="policies">Policies</option>
              <option value="contacts">Contacts</option>
              <option value="obligations">Obligations</option>
            </select>
          </div>

          <div className="mb-4">
            <label className="block text-sm font-medium mb-2">CSV Content</label>
            <textarea
              value={csvContent}
              onChange={(e) => setCsvContent(e.target.value)}
              placeholder={`Paste CSV content here...\n\nExample for agreements:\ntitle,status,governing_law\n"My NDA",draft,Sri Lanka\n"Service Agreement",draft,Singapore`}
              className="w-full h-48 border rounded px-3 py-2 font-mono text-sm"
            />
          </div>

          <button
            onClick={handleImport}
            disabled={loading || !csvContent.trim()}
            className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "Importing..." : "📥 Import Data"}
          </button>
        </div>
      )}

      {/* Export Tab */}
      {activeTab === "export" && (
        <div className="bg-white rounded-lg border p-6">
          <h2 className="text-lg font-semibold mb-4">Export Data</h2>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { type: "agreements", label: "📄 Agreements", format: "csv" },
              { type: "compliance", label: "✅ Compliance", format: "csv" },
              { type: "violations", label: "⚠️ Violations", format: "csv" },
              { type: "obligations", label: "📋 Obligations", format: "csv" },
            ].map((item) => (
              <button
                key={item.type}
                onClick={() => handleExport(item.format)}
                disabled={loading}
                className="border rounded-lg p-4 text-left hover:bg-gray-50 disabled:opacity-50"
              >
                <div className="text-lg mb-1">{item.label}</div>
                <div className="text-sm text-gray-500">Export as {item.format.toUpperCase()}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Mass Actions Tab */}
      {activeTab === "actions" && (
        <div className="bg-white rounded-lg border p-6">
          <h2 className="text-lg font-semibold mb-4">Mass Actions</h2>

          <div className="mb-4">
            <label className="block text-sm font-medium mb-2">Action</label>
            <select
              value={selectedAction}
              onChange={(e) => setSelectedAction(e.target.value)}
              className="w-full border rounded px-3 py-2"
            >
              <option value="status_change">Status Change</option>
              <option value="archive">Archive</option>
              <option value="delete">Soft Delete</option>
              <option value="compliance_check">Run Compliance Check</option>
            </select>
          </div>

          {selectedAction === "status_change" && (
            <div className="mb-4">
              <label className="block text-sm font-medium mb-2">New Status</label>
              <select
                value={actionOptions.new_status || "draft"}
                onChange={(e) => setActionOptions({ ...actionOptions, new_status: e.target.value })}
                className="w-full border rounded px-3 py-2"
              >
                <option value="draft">Draft</option>
                <option value="internal_review">Internal Review</option>
                <option value="sent">Sent</option>
                <option value="archived">Archived</option>
              </select>
            </div>
          )}

          <div className="bg-yellow-50 border border-yellow-200 rounded p-4 mb-4">
            <p className="text-sm text-yellow-800">
              ⚠️ This action will affect all agreements matching the current filters. This cannot be undone.
            </p>
          </div>

          <button
            onClick={handleBulkAction}
            disabled={loading}
            className="bg-orange-600 text-white px-6 py-2 rounded hover:bg-orange-700 disabled:opacity-50"
          >
            {loading ? "Processing..." : "⚡ Execute Action"}
          </button>
        </div>
      )}

      {/* History Tab */}
      {activeTab === "history" && (
        <div className="bg-white rounded-lg border p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Job History</h2>
            <button onClick={loadJobs} className="text-sm text-blue-600 hover:underline">
              🔄 Refresh
            </button>
          </div>

          {jobs.length === 0 ? (
            <p className="text-gray-500">No bulk jobs yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-2">Job ID</th>
                    <th className="text-left py-2">Type</th>
                    <th className="text-left py-2">Status</th>
                    <th className="text-right py-2">Total</th>
                    <th className="text-right py-2">Success</th>
                    <th className="text-right py-2">Failed</th>
                    <th className="text-left py-2">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job) => (
                    <tr key={job.id} className="border-b hover:bg-gray-50">
                      <td className="py-2 font-mono text-xs">{job.id.slice(0, 8)}...</td>
                      <td className="py-2 capitalize">{job.job_type.replace("_", " ")}</td>
                      <td className="py-2">
                        <span className={`px-2 py-1 rounded text-xs ${getStatusColor(job.status)}`}>
                          {job.status}
                        </span>
                      </td>
                      <td className="py-2 text-right">{job.total_items}</td>
                      <td className="py-2 text-right text-green-600">{job.successful_items}</td>
                      <td className="py-2 text-right text-red-600">{job.failed_items}</td>
                      <td className="py-2 text-gray-500">
                        {job.created_at ? new Date(job.created_at).toLocaleString() : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function BulkPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-64">Loading...</div>}>
      <BulkPageContent />
    </Suspense>
  );
}
