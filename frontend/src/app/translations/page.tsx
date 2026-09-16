"use client";

import { Suspense, useCallback } from "react";
import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  listAgreements,
  getVersionTranslations,
  bulkSyncTranslations,
  getTranslationDashboard,
} from "@/lib/api";

interface TranslationDashboard {
  agreement_id: string;
  primary_language: string;
  secondary_languages: string[];
  total_versions: number;
  total_translations: number;
  versions: Array<{
    version_id: string;
    version_number: number;
    status: string;
    created_at: string | null;
    translation_sync: Record<string, unknown> | null;
    translated_languages: Array<{
      code: string;
      status: string;
      quality_score: number | null;
    }>;
  }>;
}

interface TranslationStatus {
  version_id: string;
  primary_language: string;
  secondary_languages: string[];
  translations: Record<string, {
    status: string;
    quality_score: number | null;
    last_synced: string | null;
    is_outdated: boolean;
  }>;
  summary: {
    total: number;
    synced: number;
    outdated: number;
    pending: number;
  };
}

function TranslationsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { token } = useAuth();
  const [agreements, setAgreements] = useState<Array<{ id: string; title: string }>>([]);
  const [selectedAgreement, setSelectedAgreement] = useState<string>(searchParams.get("agreement") || "");
  const [dashboard, setDashboard] = useState<TranslationDashboard | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<string>("");
  const [translationStatus, setTranslationStatus] = useState<TranslationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  // Loaders declared before the effects that call them (block-scoped consts
  // can't be referenced before declaration); no leading setLoading(true) so
  // mount effects never cascade synchronously.
  const loadAgreements = useCallback(async () => {
    if (!token) return;
    try {
      const data = await listAgreements(token);
      setAgreements(data);
    } catch (err) {
      console.error("Failed to load agreements:", err);
    }
  }, [token]);

  const loadDashboard = useCallback(async () => {
    if (!token || !selectedAgreement) return;
    try {
      const data = await getTranslationDashboard(token, selectedAgreement);
      setDashboard(data);
      if (data.versions.length > 0) {
        setSelectedVersion(data.versions[0].version_id);
      }
    } catch (err) {
      console.error("Failed to load dashboard:", err);
    } finally {
      setLoading(false);
    }
  }, [token, selectedAgreement]);

  const loadTranslationStatus = useCallback(async () => {
    if (!token || !selectedAgreement || !selectedVersion) return;
    try {
      const data = await getVersionTranslations(token, selectedAgreement, selectedVersion);
      setTranslationStatus(data);
    } catch (err) {
      console.error("Failed to load translation status:", err);
    }
  }, [token, selectedAgreement, selectedVersion]);

  useEffect(() => {
    if (!token) {
      router.push("/login");
      return;
    }
    queueMicrotask(() => loadAgreements());
  }, [token, loadAgreements, router]);

  useEffect(() => {
    if (selectedAgreement && token) {
      queueMicrotask(() => loadDashboard());
    }
  }, [selectedAgreement, token, loadDashboard]);

  useEffect(() => {
    if (selectedVersion && selectedAgreement && token) {
      queueMicrotask(() => loadTranslationStatus());
    }
  }, [selectedVersion, selectedAgreement, token, loadTranslationStatus]);

  const handleBulkSync = async (versionId: string) => {
    if (!token || !selectedAgreement) return;
    setSyncing(true);
    try {
      const result = await bulkSyncTranslations(token, selectedAgreement, {
        to_version_id: versionId,
      });
      alert(`Bulk sync: ${result.message}`);
      loadDashboard();
    } catch (err) {
      alert(`Bulk sync failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setSyncing(false);
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "synced":
      case "approved":
      case "published":
        return "text-green-600 bg-green-50";
      case "outdated":
      case "in_review":
        return "text-yellow-600 bg-yellow-50";
      case "pending":
      case "draft":
        return "text-gray-600 bg-gray-50";
      default:
        return "text-gray-600 bg-gray-50";
    }
  };

  const getLanguageName = (code: string) => {
    const names: Record<string, string> = {
      en: "English",
      si: "Sinhala",
      ta: "Tamil",
      zh: "Chinese",
      ar: "Arabic",
      hi: "Hindi",
      ja: "Japanese",
      de: "German",
    };
    return names[code] || code;
  };

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">🌍 Translation Sync</h1>
          <p className="text-gray-500 mt-1">Manage translations across contract versions</p>
        </div>
        <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
          ← Back
        </button>
      </div>

      {/* Agreement Selector */}
      <div className="bg-white rounded-lg border p-4 mb-6">
        <label className="block text-sm font-medium mb-2">Select Agreement</label>
        <select
          value={selectedAgreement}
          onChange={(e) => setSelectedAgreement(e.target.value)}
          className="w-full border rounded px-3 py-2"
        >
          <option value="">Choose an agreement...</option>
          {agreements.map((a) => (
            <option key={a.id} value={a.id}>
              {a.title}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading...</div>
      ) : dashboard ? (
        <>
          {/* Overview Cards */}
          <div className="grid grid-cols-4 gap-4 mb-6">
            <div className="bg-white rounded-lg border p-4">
              <div className="text-2xl font-bold">{dashboard.total_versions}</div>
              <div className="text-sm text-gray-500">Versions</div>
            </div>
            <div className="bg-white rounded-lg border p-4">
              <div className="text-2xl font-bold">{dashboard.total_translations}</div>
              <div className="text-sm text-gray-500">Translations</div>
            </div>
            <div className="bg-white rounded-lg border p-4">
              <div className="text-2xl font-bold text-blue-600">{dashboard.primary_language}</div>
              <div className="text-sm text-gray-500">Primary Language</div>
            </div>
            <div className="bg-white rounded-lg border p-4">
              <div className="text-2xl font-bold text-purple-600">{dashboard.secondary_languages.length}</div>
              <div className="text-sm text-gray-500">Secondary Languages</div>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-6">
            {/* Version List */}
            <div className="bg-white rounded-lg border">
              <div className="p-4 border-b">
                <h2 className="font-semibold">Versions</h2>
              </div>
              <div className="divide-y max-h-96 overflow-y-auto">
                {dashboard.versions.map((v) => (
                  <div
                    key={v.version_id}
                    onClick={() => setSelectedVersion(v.version_id)}
                    className={`p-4 cursor-pointer hover:bg-gray-50 ${
                      selectedVersion === v.version_id ? "bg-blue-50 border-l-4 border-blue-500" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="font-medium">Version {v.version_number}</div>
                        <div className="text-xs text-gray-500">
                          {v.created_at ? new Date(v.created_at).toLocaleDateString() : "—"}
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        {v.translated_languages.length > 0 ? (
                          v.translated_languages.map((tl) => (
                            <span
                              key={tl.code}
                              className={`text-xs px-1.5 py-0.5 rounded ${getStatusColor(tl.status)}`}
                            >
                              {tl.code.toUpperCase()}
                            </span>
                          ))
                        ) : (
                          <span className="text-xs text-gray-400">No translations</span>
                        )}
                      </div>
                    </div>

                    {/* Translation sync info */}
                    {v.translation_sync && (
                      <div className="mt-2 text-xs text-gray-500">
                        {v.translation_sync.status === "completed" && (
                          <span className="text-green-600">✓ All synced</span>
                        )}
                        {v.translation_sync.status === "partial" && (
                          <span className="text-yellow-600">
                            ⚠ {(v.translation_sync as Record<string, unknown> | null)?.message as string | undefined || "Needs review"}
                          </span>
                        )}
                        {v.translation_sync.status === "pending" && (
                          <span className="text-gray-500">⏳ {(v.translation_sync as Record<string, unknown> | null)?.message as string | undefined || "Pending"}</span>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Translation Status */}
            <div className="col-span-2 bg-white rounded-lg border">
              <div className="p-4 border-b flex items-center justify-between">
                <h2 className="font-semibold">
                  Translation Status
                  {selectedVersion && (
                    <span className="text-sm font-normal text-gray-500 ml-2">
                      (Version {dashboard.versions.find(v => v.version_id === selectedVersion)?.version_number})
                    </span>
                  )}
                </h2>
                {selectedVersion && (
                  <button
                    onClick={() => handleBulkSync(selectedVersion)}
                    disabled={syncing}
                    className="text-sm bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700 disabled:opacity-50"
                  >
                    {syncing ? "Syncing..." : "🔄 Sync All"}
                  </button>
                )}
              </div>

              {translationStatus ? (
                <div className="p-4">
                  {/* Summary */}
                  <div className="flex items-center gap-4 mb-6">
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full bg-green-500"></div>
                      <span className="text-sm">
                        Synced: {translationStatus.summary.synced}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full bg-yellow-500"></div>
                      <span className="text-sm">
                        Outdated: {translationStatus.summary.outdated}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full bg-gray-400"></div>
                      <span className="text-sm">
                        Pending: {translationStatus.summary.pending}
                      </span>
                    </div>
                  </div>

                  {/* Language Grid */}
                  <div className="grid grid-cols-2 gap-4">
                    {/* Primary Language */}
                    <div className="border rounded-lg p-4 bg-blue-50">
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-medium">{getLanguageName(translationStatus.primary_language)}</span>
                        <span className="text-xs px-2 py-0.5 bg-blue-200 rounded">PRIMARY</span>
                      </div>
                      {translationStatus.translations[translationStatus.primary_language] ? (
                        <div className="text-sm">
                          <span className={`px-2 py-1 rounded ${getStatusColor(translationStatus.translations[translationStatus.primary_language].status)}`}>
                            {translationStatus.translations[translationStatus.primary_language].status}
                          </span>
                        </div>
                      ) : (
                        <div className="text-sm text-gray-500">Source language</div>
                      )}
                    </div>

                    {/* Secondary Languages */}
                    {translationStatus.secondary_languages.map((langCode) => {
                      const trans = translationStatus.translations[langCode];
                      return (
                        <div key={langCode} className="border rounded-lg p-4">
                          <div className="flex items-center justify-between mb-2">
                            <span className="font-medium">{getLanguageName(langCode)}</span>
                            {trans?.is_outdated && (
                              <span className="text-xs px-2 py-0.5 bg-yellow-200 rounded">
                                NEEDS REVIEW
                              </span>
                            )}
                          </div>
                          {trans ? (
                            <div className="space-y-1">
                              <span className={`text-xs px-2 py-1 rounded ${getStatusColor(trans.status)}`}>
                                {trans.status}
                              </span>
                              {trans.quality_score && (
                                <div className="text-xs text-gray-500">
                                  Quality: {trans.quality_score}%
                                </div>
                              )}
                              {trans.last_synced && (
                                <div className="text-xs text-gray-500">
                                  Last synced: {new Date(trans.last_synced).toLocaleDateString()}
                                </div>
                              )}
                            </div>
                          ) : (
                            <div className="text-sm text-gray-500">Not translated yet</div>
                          )}
                        </div>
                      );
                    })}

                    {translationStatus.secondary_languages.length === 0 && (
                      <div className="border rounded-lg p-4 border-dashed">
                        <div className="text-center text-gray-500">
                          <div className="text-2xl mb-2">🌐</div>
                          <div className="text-sm">No secondary languages configured</div>
                          <button
                            onClick={() => router.push("/i18n")}
                            className="text-blue-600 text-sm mt-1 hover:underline"
                          >
                            Configure languages →
                          </button>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Sync Actions */}
                  {dashboard.versions.length >= 2 && (
                    <div className="mt-6 p-4 bg-gray-50 rounded-lg">
                      <h3 className="font-medium mb-3">Sync Between Versions</h3>
                      <div className="flex items-center gap-4">
                        <select className="border rounded px-3 py-2 text-sm">
                          {dashboard.versions.map((v) => (
                            <option key={v.version_id} value={v.version_id}>
                              Version {v.version_number}
                            </option>
                          ))}
                        </select>
                        <span className="text-gray-400">→</span>
                        <select className="border rounded px-3 py-2 text-sm">
                          {dashboard.versions.map((v) => (
                            <option key={v.version_id} value={v.version_id}>
                              Version {v.version_number}
                            </option>
                          ))}
                        </select>
                        <button
                          disabled={syncing}
                          className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700 disabled:opacity-50"
                        >
                          {syncing ? "Syncing..." : "Sync"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="p-8 text-center text-gray-500">
                  Select a version to view translation status
                </div>
              )}
            </div>
          </div>
        </>
      ) : (
        <div className="bg-white rounded-lg border p-8 text-center">
          <div className="text-4xl mb-4">🌍</div>
          <h2 className="text-xl font-semibold mb-2">Translation Sync Dashboard</h2>
          <p className="text-gray-500 mb-6">
            Select an agreement to manage translations across versions.
          </p>
        </div>
      )}
    </div>
  );
}

export default function TranslationsPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-64">Loading...</div>}>
      <TranslationsContent />
    </Suspense>
  );
}
