"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useLanguage, LanguageSwitcher } from "@/components/LanguageSwitcher";

interface Language {
  id: string;
  code: string;
  name: string;
  native_name: string;
  locale: string;
  direction: string;
  date_format: string;
  currency: string;
  legal_systems: string[];
  supported_jurisdictions: string[];
  is_beta: boolean;
}

interface GlossaryTerm {
  source_term: string;
  translations: Record<string, string>;
  category: string;
  definition: string;
  usage_count: number;
}

export default function I18nPage() {
  const router = useRouter();
  const { token } = useAuth();
  const { language, setLanguage } = useLanguage();
  const [languages, setLanguages] = useState<Language[]>([]);
  const [glossary, setGlossary] = useState<GlossaryTerm[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"languages" | "glossary" | "document">("languages");

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [langRes, glossaryRes] = await Promise.all([
        fetch("/api/v1/i18n/languages").then((r) => r.json()),
        fetch(`/api/v1/i18n/glossary?language=${language}`).then((r) => r.json()),
      ]);
      setLanguages(langRes);
      setGlossary(glossaryRes);
    } catch (err) {
      console.error("Failed to load i18n data:", err);
    } finally {
      setLoading(false);
    }
  }, [language]);

  useEffect(() => {
    if (!token) {
      router.push("/login");
      return;
    }
    void Promise.resolve().then(loadData);
  }, [token, router, loadData]);

  const getDirectionIcon = (dir: string) => (dir === "rtl" ? "←" : "→");

  const formatCurrency = (code: string) => {
    const currencies: Record<string, string> = {
      USD: "$", LKR: "Rs.", EUR: "€", GBP: "£", JPY: "¥", CNY: "¥", SAR: "﷼", INR: "₹",
    };
    return currencies[code] || code;
  };

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">🌍 Internationalization</h1>
          <p className="text-gray-500 mt-1">Manage languages and translations for contracts</p>
        </div>
        <div className="flex items-center gap-4">
          <LanguageSwitcher />
          <button onClick={() => router.push("/dashboard")} className="text-blue-600 hover:underline">
            ← Back
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex space-x-1 mb-6 border-b">
        {(["languages", "glossary", "document"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium capitalize ${
              activeTab === tab
                ? "border-b-2 border-blue-600 text-blue-600"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {tab === "languages" ? "🌐 Languages" : tab === "glossary" ? "📖 Legal Glossary" : "📄 Document Locale"}
          </button>
        ))}
      </div>

      {/* Languages Tab */}
      {activeTab === "languages" && (
        <div className="bg-white rounded-lg border">
          <div className="p-4 border-b">
            <h2 className="text-lg font-semibold">Supported Languages ({languages.length})</h2>
          </div>

          {loading ? (
            <div className="p-8 text-center text-gray-500">Loading...</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4">
              {languages.map((lang) => (
                <div
                  key={lang.code}
                  className={`border rounded-lg p-4 cursor-pointer transition-all ${
                    language === lang.code
                      ? "border-blue-500 bg-blue-50 ring-2 ring-blue-200"
                      : "hover:border-gray-300"
                  }`}
                  onClick={() => setLanguage(lang.code)}
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-lg font-medium">{lang.name}</span>
                        <span className="text-sm text-gray-500">{lang.native_name}</span>
                        {lang.is_beta && (
                          <span className="text-xs px-2 py-0.5 bg-yellow-100 text-yellow-700 rounded">
                            Beta
                          </span>
                        )}
                      </div>
                      <div className="text-sm text-gray-500 mt-1">
                        {lang.locale} • {lang.direction.toUpperCase()}
                      </div>
                    </div>
                    {language === lang.code && (
                      <span className="text-blue-600">✓</span>
                    )}
                  </div>

                  <div className="mt-3 flex flex-wrap gap-2">
                    <span className="text-xs px-2 py-1 bg-gray-100 rounded">
                      {formatCurrency(lang.currency)} {lang.currency}
                    </span>
                    <span className="text-xs px-2 py-1 bg-gray-100 rounded">
                      📅 {lang.date_format}
                    </span>
                    <span className="text-xs px-2 py-1 bg-gray-100 rounded">
                      {getDirectionIcon(lang.direction)} {lang.direction.toUpperCase()}
                    </span>
                  </div>

                  <div className="mt-2 flex flex-wrap gap-1">
                    {lang.supported_jurisdictions.map((j) => (
                      <span key={j} className="text-xs px-2 py-0.5 bg-blue-50 text-blue-700 rounded">
                        {j}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Glossary Tab */}
      {activeTab === "glossary" && (
        <div className="bg-white rounded-lg border">
          <div className="p-4 border-b flex items-center justify-between">
            <h2 className="text-lg font-semibold">Legal Glossary ({glossary.length} terms)</h2>
            <button
              onClick={() => {
                const term = prompt("Enter English term:");
                if (term) {
                  const translation = prompt(`Enter ${language} translation:`);
                  if (translation) {
                    fetch("/api/v1/i18n/glossary", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        source_term: term,
                        translations: { [language]: translation },
                      }),
                    }).then(() => loadData());
                  }
                }
              }}
              className="text-sm bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700"
            >
              + Add Term
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left py-3 px-4">English</th>
                  <th className="text-left py-3 px-4">
                    Translation ({language.toUpperCase()})
                  </th>
                  <th className="text-left py-3 px-4">Category</th>
                  <th className="text-left py-3 px-4">Definition</th>
                </tr>
              </thead>
              <tbody>
                {glossary.map((term) => (
                  <tr key={term.source_term} className="border-b hover:bg-gray-50">
                    <td className="py-3 px-4 font-medium">{term.source_term}</td>
                    <td className="py-3 px-4">
                      {term.translations?.[language] || (
                        <span className="text-gray-400 italic">Not translated</span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <span className="px-2 py-0.5 bg-gray-100 rounded text-xs capitalize">
                        {term.category || "legal"}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-gray-600 text-xs max-w-xs truncate">
                      {term.definition}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {glossary.length === 0 && !loading && (
            <div className="p-8 text-center text-gray-500">
              No glossary terms found. Click &quot;Add Term&quot; to create one.
            </div>
          )}
        </div>
      )}

      {/* Document Locale Tab */}
      {activeTab === "document" && (
        <div className="bg-white rounded-lg border p-6">
          <h2 className="text-lg font-semibold mb-4">Document Locale Settings</h2>
          <p className="text-gray-500 mb-6">
            Configure language settings for individual agreements. Access this from the agreement detail page.
          </p>

          <div className="grid grid-cols-2 gap-6">
            <div>
              <h3 className="font-medium mb-3">Supported Formats</h3>
              <div className="space-y-2">
                <div className="flex items-center gap-3 p-3 bg-gray-50 rounded">
                  <span>📅</span>
                  <div>
                    <div className="font-medium">Date Formats</div>
                    <div className="text-sm text-gray-500">DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD</div>
                  </div>
                </div>
                <div className="flex items-center gap-3 p-3 bg-gray-50 rounded">
                  <span>💰</span>
                  <div>
                    <div className="font-medium">Currency Formats</div>
                    <div className="text-sm text-gray-500">$1,000 • Rs. 1,000 • €1.000</div>
                  </div>
                </div>
                <div className="flex items-center gap-3 p-3 bg-gray-50 rounded">
                  <span>📝</span>
                  <div>
                    <div className="font-medium">Number Formats</div>
                    <div className="text-sm text-gray-500">1,000.00 • 1.000,00</div>
                  </div>
                </div>
              </div>
            </div>

            <div>
              <h3 className="font-medium mb-3">Text Direction</h3>
              <div className="space-y-2">
                <div className="flex items-center gap-3 p-3 bg-gray-50 rounded">
                  <span>→</span>
                  <div>
                    <div className="font-medium">Left-to-Right (LTR)</div>
                    <div className="text-sm text-gray-500">English, Sinhala, Tamil, Chinese, Hindi, Japanese</div>
                  </div>
                </div>
                <div className="flex items-center gap-3 p-3 bg-gray-50 rounded">
                  <span>←</span>
                  <div>
                    <div className="font-medium">Right-to-Left (RTL)</div>
                    <div className="text-sm text-gray-500">Arabic, Hebrew, Persian</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-6 p-4 bg-blue-50 rounded-lg">
            <h3 className="font-medium text-blue-900 mb-2">Multi-Language Contracts</h3>
            <p className="text-sm text-blue-700">
              ContractOS supports side-by-side translations for international agreements.
              When creating an agreement, select the primary language and any secondary languages
              for translated versions. The governing language clause specifies which version
              controls in case of discrepancies.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
