"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  createIngestionJob,
  getIngestionReviewQueue,
  decideIngestionReview,
  listIngestionJobs,
  uploadIngestionDocument,
} from "@/lib/api";

// ─── Types ───────────────────────────────────────────────────────────────────

interface UploadFile {
  id: string;
  file: File;
  status: "pending" | "uploading" | "done" | "error";
  confidence?: number;
  needsReview?: boolean;
  error?: string;
}

interface ReviewItem {
  id: string;
  ocr_document_id: string;
  filename: string | null;
  confidence: number;
  status: string;
  extracted_text_preview: string | null;
  created_at: string | null;
}

interface IngestionJob {
  id: string;
  source: string;
  status: string;
  total_files: number;
  completed_files: number;
  failed_files: number;
  created_at: string | null;
}

type Tab = "upload" | "review" | "jobs";

// ─── Helpers ─────────────────────────────────────────────────────────────────

const confColor = (c: number) => {
  if (c >= 0.9) return "text-emerald-700 bg-emerald-50 border-emerald-200";
  if (c >= 0.8) return "text-amber-700 bg-amber-50 border-amber-200";
  return "text-rose-700 bg-rose-50 border-rose-200";
};

const confLabel = (c: number) => {
  if (c >= 0.9) return "High";
  if (c >= 0.8) return "Medium";
  return "Low — Review Required";
};

// ─── Main component ───────────────────────────────────────────────────────────

export default function IngestionPage() {
  const { user, token, isLoading } = useAuth();
  const router = useRouter();

  const [tab, setTab] = useState<Tab>("upload");
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  // Product decision: real cloud OCR first with automatic fallback. The
  // mock engine stays available for explicit dev/testing selection only —
  // it is refused outright when the backend runs in production.
  const [provider, setProvider] = useState<string>("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const [reviewQueue, setReviewQueue] = useState<ReviewItem[]>([]);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [correctedText, setCorrectedText] = useState<Record<string, string>>({});
  const [expandedItem, setExpandedItem] = useState<string | null>(null);

  const [jobs, setJobs] = useState<IngestionJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isLoading && !token) router.replace("/login");
    if (!isLoading && token && user && !user.is_admin) router.replace("/dashboard");
  }, [isLoading, token, user, router]);

  const loadReviewQueue = useCallback(async () => {
    if (!token) return;
    setReviewLoading(true);
    try {
      const data = await getIngestionReviewQueue(token);
      setReviewQueue(data);
    } catch (e) {
      console.error(e);
    } finally {
      setReviewLoading(false);
    }
  }, [token]);

  const loadJobs = useCallback(async () => {
    if (!token) return;
    setJobsLoading(true);
    try {
      const data = await listIngestionJobs(token);
      setJobs(data);
    } finally {
      setJobsLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    if (tab === "review") loadReviewQueue();
    if (tab === "jobs") loadJobs();
  }, [tab, token, loadReviewQueue, loadJobs]);

  const addFiles = (incoming: FileList | File[]) => {
    const arr = Array.from(incoming);
    const newFiles: UploadFile[] = arr.map((f) => ({
      id: `${f.name}-${Date.now()}-${Math.random()}`,
      file: f,
      status: "pending",
    }));
    setFiles((prev) => [...prev, ...newFiles]);
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
  }, []);

  const startUpload = async () => {
    if (!token || files.length === 0) return;
    setUploading(true);

    let activeJobId = jobId;
    if (!activeJobId) {
      try {
        const job = await createIngestionJob(token, "upload");
        activeJobId = job.id;
        setJobId(job.id);
      } catch (e: unknown) {
        alert(e instanceof Error ? e.message : "Failed to create job");
        setUploading(false);
        return;
      }
    }

    for (const uf of files.filter((f) => f.status === "pending")) {
      setFiles((prev) => prev.map((f) => (f.id === uf.id ? { ...f, status: "uploading" } : f)));
      try {
        const res = await uploadIngestionDocument(token, activeJobId, uf.file, provider);
        setFiles((prev) =>
          prev.map((f) =>
            f.id === uf.id
              ? { ...f, status: "done", confidence: res.confidence, needsReview: res.needs_review }
              : f
          )
        );
      } catch (e: unknown) {
        setFiles((prev) =>
          prev.map((f) =>
            f.id === uf.id ? { ...f, status: "error", error: e instanceof Error ? e.message : "Upload failed" } : f
          )
        );
      }
    }
    setUploading(false);
  };

  const decide = async (item: ReviewItem, approved: boolean) => {
    if (!token) return;
    setDecidingId(item.id);
    try {
      await decideIngestionReview(token, item.id, approved, correctedText[item.id]);
      setReviewQueue((prev) => prev.filter((r) => r.id !== item.id));
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Decision failed");
    } finally {
      setDecidingId(null);
    }
  };

  if (isLoading || !user?.is_admin) return null;

  const pendingCount = files.filter((f) => f.status === "pending").length;
  const pendingReview = reviewQueue.filter((r) => r.status === "pending").length;

  return (
    <div className="min-h-screen font-sans text-gray-900 bg-transparent animate-fade-in">
      <div className="orb w-96 h-96 -top-20 -left-20 bg-gradient-to-br from-indigo-200/50 to-purple-200/40 animate-float" />
      <div className="orb w-72 h-72 top-1/2 -right-16 bg-gradient-to-bl from-brand-200/50 to-indigo-100/30 animate-float" style={{ animationDelay: "2s" }} />

      <header className="fixed top-0 left-0 right-0 z-50 px-4 py-4">
        <div className="max-w-7xl mx-auto glass-panel rounded-full px-6 py-3 flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <Link href="/admin" className="text-sm font-semibold text-gray-500 hover:text-brand-600 transition-colors flex items-center gap-1">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"/></svg>
              Admin Console
            </Link>
            <span className="w-px h-4 bg-gray-300" />
            <span className="text-lg font-bold bg-clip-text text-transparent bg-gradient-to-r from-indigo-600 to-purple-500">Contract Ingestion</span>
          </div>
          <span className="px-3 py-1 text-[11px] uppercase tracking-widest font-bold rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 text-white shadow">OCR Pipeline</span>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-32 pb-16">
        {/* Tab bar */}
        <div className="flex items-center gap-1 bg-white/30 backdrop-blur-sm border border-white/40 p-1.5 rounded-2xl mb-8 w-fit shadow">
          {(["upload", "review", "jobs"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-5 py-2 text-sm font-bold rounded-xl transition-all duration-200 flex items-center gap-1.5 ${
                tab === t
                  ? "bg-gradient-to-r from-indigo-600 to-purple-500 text-white shadow-md"
                  : "text-gray-500 hover:text-gray-800 hover:bg-white/60"
              }`}
            >
              {t === "upload" && "📤 Upload Documents"}
              {t === "review" && (
                <>
                  🔍 Review Queue
                  {pendingReview > 0 && (
                    <span className="px-1.5 py-0.5 text-[10px] bg-rose-500 text-white rounded-full">{pendingReview}</span>
                  )}
                </>
              )}
              {t === "jobs" && "📋 Job History"}
            </button>
          ))}
        </div>

        {/* ── UPLOAD ── */}
        {tab === "upload" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 space-y-6">
              {/* Drop zone */}
              <div
                onDrop={onDrop}
                onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onClick={() => fileInputRef.current?.click()}
                className={`relative border-2 border-dashed rounded-3xl p-12 text-center cursor-pointer transition-all duration-300 ${
                  isDragging
                    ? "border-indigo-400 bg-indigo-50/60 scale-[1.01] shadow-xl"
                    : "border-gray-200/80 bg-white/30 hover:border-indigo-300 hover:bg-white/50"
                }`}
              >
                <input ref={fileInputRef} type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.tiff" className="hidden"
                  onChange={(e) => e.target.files && addFiles(e.target.files)} />
                <div className={`w-20 h-20 mx-auto mb-6 rounded-3xl flex items-center justify-center transition-all ${isDragging ? "bg-indigo-100 scale-110" : "bg-gray-100"}`}>
                  <svg className={`w-10 h-10 transition-colors ${isDragging ? "text-indigo-500" : "text-gray-400"}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
                  </svg>
                </div>
                <p className="text-xl font-bold text-gray-700 mb-2">{isDragging ? "Drop to queue files" : "Drag & drop scanned contracts"}</p>
                <p className="text-sm text-gray-500">PDF, PNG, JPG, TIFF — up to 50 MB each</p>
                <div className="mt-6 inline-flex items-center gap-2 px-5 py-2 bg-gradient-to-r from-indigo-600 to-purple-500 text-white text-sm font-bold rounded-full shadow-md">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4"/></svg>
                  Browse Files
                </div>
              </div>

              {/* File list */}
              {files.length > 0 && (
                <div className="glass-card rounded-2xl overflow-hidden">
                  <div className="px-6 py-4 border-b border-gray-100/50 flex items-center justify-between">
                    <h3 className="font-bold text-gray-900">Queued Files ({files.length})</h3>
                    <button onClick={() => setFiles([])} className="text-xs text-gray-400 hover:text-rose-500 transition-colors">Clear all</button>
                  </div>
                  <ul className="divide-y divide-gray-100/50">
                    {files.map((uf) => (
                      <li key={uf.id} className="px-6 py-4 flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3 min-w-0">
                          <div className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${
                            uf.status === "done" ? "bg-emerald-50" : uf.status === "error" ? "bg-rose-50" : uf.status === "uploading" ? "bg-indigo-50" : "bg-gray-50"
                          }`}>
                            {uf.status === "uploading" && <div className="w-4 h-4 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin"/>}
                            {uf.status === "done" && <svg className="w-5 h-5 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"/></svg>}
                            {uf.status === "error" && <svg className="w-5 h-5 text-rose-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/></svg>}
                            {uf.status === "pending" && <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>}
                          </div>
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-gray-800 truncate">{uf.file.name}</p>
                            <p className="text-xs text-gray-400">{(uf.file.size / 1024).toFixed(0)} KB</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          {uf.confidence !== undefined && (
                            <span className={`px-2.5 py-1 text-[11px] font-bold rounded-full border ${confColor(uf.confidence)}`}>
                              {(uf.confidence * 100).toFixed(0)}% — {confLabel(uf.confidence)}
                            </span>
                          )}
                          {uf.status === "error" && <span className="text-xs text-rose-600 font-medium">{uf.error}</span>}
                          {uf.status === "pending" && (
                            <button onClick={(e) => { e.stopPropagation(); setFiles(prev => prev.filter(f => f.id !== uf.id)); }} className="text-gray-300 hover:text-rose-400 transition-colors">
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/></svg>
                            </button>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* Settings */}
            <div className="space-y-6">
              <div className="glass-card rounded-2xl p-6">
                <h3 className="font-bold text-gray-900 mb-5">⚙️ OCR Settings</h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs font-semibold text-gray-600 uppercase tracking-wider mb-2">Provider</label>
                    <select value={provider} onChange={(e) => setProvider(e.target.value)}
                      className="w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm bg-white/60 focus:outline-none focus:ring-2 focus:ring-indigo-300">
                      <option value="">Auto (Textract → Doc AI fallback)</option>
                      <option value="aws_textract">AWS Textract</option>
                      <option value="google_document_ai">Google Document AI</option>
                      <option value="azure_form_recognizer">Azure Form Recognizer</option>
                      <option value="mock">Mock (dev/testing only)</option>
                    </select>
                  </div>
                  <div className="rounded-xl bg-indigo-50/60 border border-indigo-100 p-4">
                    <p className="text-xs font-semibold text-indigo-700 mb-1">Auto-Review Threshold</p>
                    <p className="text-xs text-indigo-600">Documents with confidence below 80% are queued for human review.</p>
                  </div>
                  {jobId && (
                    <div className="rounded-xl bg-emerald-50 border border-emerald-100 p-3">
                      <p className="text-xs font-semibold text-emerald-700">Active Job</p>
                      <p className="text-xs text-emerald-600 font-mono mt-0.5 truncate">{jobId}</p>
                    </div>
                  )}
                </div>
              </div>
              <button onClick={startUpload} disabled={uploading || pendingCount === 0}
                className="w-full py-4 bg-gradient-to-r from-indigo-600 to-purple-500 text-white font-bold rounded-2xl shadow-lg hover:opacity-90 hover:-translate-y-0.5 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none">
                {uploading ? (
                  <span className="flex items-center justify-center gap-2">
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"/>Processing...
                  </span>
                ) : `🚀 Start OCR — ${pendingCount} file${pendingCount !== 1 ? "s" : ""}`}
              </button>
              {files.some(f => f.needsReview) && (
                <button onClick={() => setTab("review")}
                  className="w-full py-3 bg-rose-50 border border-rose-200 text-rose-700 font-bold text-sm rounded-2xl hover:bg-rose-100 transition-colors">
                  ⚠️ {files.filter(f => f.needsReview).length} file(s) need review →
                </button>
              )}
            </div>
          </div>
        )}

        {/* ── REVIEW QUEUE ── */}
        {tab === "review" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h2 className="text-xl font-bold text-gray-900">Human Review Queue</h2>
                <p className="text-sm text-gray-500 mt-0.5">Low-confidence OCR extractions awaiting correction</p>
              </div>
              <button onClick={loadReviewQueue} className="px-4 py-2 text-sm font-semibold text-indigo-600 bg-indigo-50 rounded-xl hover:bg-indigo-100 transition-colors">🔄 Refresh</button>
            </div>

            {reviewLoading ? (
              <div className="flex items-center justify-center py-20">
                <div className="w-8 h-8 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin"/>
              </div>
            ) : reviewQueue.length === 0 ? (
              <div className="glass-card rounded-3xl p-16 text-center">
                <div className="w-20 h-20 mx-auto mb-6 bg-emerald-50 rounded-full flex items-center justify-center">
                  <svg className="w-10 h-10 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                </div>
                <p className="text-xl font-bold text-gray-700 mb-1">Queue is empty</p>
                <p className="text-sm text-gray-500">All documents have been reviewed.</p>
              </div>
            ) : (
              <div className="space-y-4">
                {reviewQueue.map((item) => (
                  <div key={item.id} className="glass-card rounded-2xl overflow-hidden">
                    <div className="px-6 py-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                      <div className="flex items-center gap-4">
                        <div className="w-10 h-10 rounded-xl bg-amber-50 border border-amber-100 flex items-center justify-center shrink-0">
                          <svg className="w-5 h-5 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
                        </div>
                        <div>
                          <p className="font-bold text-gray-900">{item.filename ?? "Unnamed document"}</p>
                          <div className="flex items-center gap-2 mt-1">
                            <span className={`px-2 py-0.5 text-[11px] font-bold rounded-full border ${confColor(item.confidence)}`}>
                              {(item.confidence * 100).toFixed(0)}% confidence
                            </span>
                            {item.created_at && <span className="text-xs text-gray-400">{new Date(item.created_at).toLocaleString()}</span>}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button onClick={() => setExpandedItem(expandedItem === item.id ? null : item.id)}
                          className="px-3 py-1.5 text-xs font-semibold text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors">
                          {expandedItem === item.id ? "Collapse" : "Review Text"}
                        </button>
                        <button onClick={() => decide(item, false)} disabled={decidingId === item.id}
                          className="px-4 py-1.5 text-xs font-bold text-rose-700 bg-rose-50 border border-rose-200 rounded-lg hover:bg-rose-100 transition-colors disabled:opacity-50">
                          Reject
                        </button>
                        <button onClick={() => decide(item, true)} disabled={decidingId === item.id}
                          className="px-4 py-1.5 text-xs font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg hover:bg-emerald-100 transition-colors disabled:opacity-50">
                          {decidingId === item.id ? "Saving…" : "Approve"}
                        </button>
                      </div>
                    </div>

                    {expandedItem === item.id && (
                      <div className="border-t border-gray-100/60 px-6 py-5 bg-gray-50/40">
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                          <div>
                            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Original PDF</p>
                            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden h-[600px]">
                              <iframe
                                src={`/api/v1/ingestion/documents/${item.ocr_document_id}/pdf`}
                                className="w-full h-full"
                                title="Original Document"
                              />
                            </div>
                          </div>
                          <div className="flex flex-col h-full">
                            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Extracted Text Preview</p>
                            <pre className="text-xs text-gray-700 bg-white border border-gray-200 rounded-xl p-4 overflow-y-auto whitespace-pre-wrap font-mono leading-relaxed mb-4 flex-1">
                              {item.extracted_text_preview ?? "(No preview available)"}
                            </pre>
                            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Corrected Text (optional)</p>
                            <textarea value={correctedText[item.id] ?? ""} onChange={(e) => setCorrectedText(prev => ({ ...prev, [item.id]: e.target.value }))}
                              placeholder="Paste corrected text here if the extraction needs fixing…"
                              className="w-full border border-gray-200 rounded-xl px-4 py-3 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-y min-h-[150px] font-mono"/>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── JOBS ── */}
        {tab === "jobs" && (
          <div className="glass-card rounded-2xl overflow-hidden">
            <div className="px-6 py-5 border-b border-gray-100/50 flex items-center justify-between">
              <div>
                <h2 className="text-xl font-bold text-gray-900">Ingestion Job History</h2>
                <p className="text-sm text-gray-500 mt-0.5">All OCR ingestion batches</p>
              </div>
              <button onClick={loadJobs} className="px-4 py-2 text-sm font-semibold text-indigo-600 bg-indigo-50 rounded-xl hover:bg-indigo-100 transition-colors">🔄 Refresh</button>
            </div>
            {jobsLoading ? (
              <div className="flex items-center justify-center py-16"><div className="w-8 h-8 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin"/></div>
            ) : jobs.length === 0 ? (
              <div className="px-6 py-16 text-center"><p className="text-gray-500 font-medium">No ingestion jobs yet.</p></div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-100/50">
                  <thead className="bg-gray-50/30">
                    <tr>
                      {["Job ID", "Source", "Status", "Progress", "Done", "Failed", "Created"].map(h => (
                        <th key={h} className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100/50 bg-white/10">
                    {jobs.map((j) => {
                      const pct = j.total_files > 0 ? Math.round((j.completed_files / j.total_files) * 100) : 0;
                      return (
                        <tr key={j.id} className="row-hover">
                          <td className="px-6 py-4 text-xs font-mono text-gray-500">{j.id.slice(0, 8)}…</td>
                          <td className="px-6 py-4 text-sm font-medium text-gray-700 capitalize">{j.source}</td>
                          <td className="px-6 py-4">
                            <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border shadow-sm ${
                              j.status === "completed" ? "bg-emerald-100 text-emerald-800 border-emerald-200" :
                              j.status === "processing" ? "bg-blue-100 text-blue-800 border-blue-200" :
                              j.status === "failed" ? "bg-rose-100 text-rose-800 border-rose-200" :
                              "bg-gray-100 text-gray-700 border-gray-200"
                            }`}>{j.status}</span>
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex items-center gap-2">
                              <div className="w-20 bg-gray-200 rounded-full h-1.5">
                                <div className="bg-indigo-500 h-1.5 rounded-full transition-all" style={{ width: `${pct}%` }}/>
                              </div>
                              <span className="text-xs text-gray-500">{pct}%</span>
                            </div>
                          </td>
                          <td className="px-6 py-4 text-sm font-bold text-emerald-600">{j.completed_files}</td>
                          <td className="px-6 py-4 text-sm font-bold text-rose-500">{j.failed_files}</td>
                          <td className="px-6 py-4 text-xs text-gray-500">{j.created_at ? new Date(j.created_at).toLocaleString() : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
