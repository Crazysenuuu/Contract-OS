"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { listJurisdictions } from "@/lib/api";

interface Jurisdiction {
  id: string;
  code: string;
  name: string;
  region: string | null;
  language: string;
  legal_system: string | null;
  currency: string;
  timezone: string;
  required_clauses: string[] | null;
  prohibited_clauses: string[] | null;
  signature_requirements: Record<string, unknown> | null;
  default_dispute_resolution: string | null;
  is_active: boolean;
}

const regionColors: Record<string, string> = {
  "South Asia": "bg-green-100 text-green-800",
  "Southeast Asia": "bg-blue-100 text-blue-800",
  "North America": "bg-purple-100 text-purple-800",
  "Europe": "bg-yellow-100 text-yellow-800",
};

export default function JurisdictionsPage() {
  const { token } = useAuth();
  const [jurisdictions, setJurisdictions] = useState<Jurisdiction[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Jurisdiction | null>(null);

  useEffect(() => {
    if (token) {
      listJurisdictions(token)
        .then(setJurisdictions)
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token]);

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  return (
    <div>
      <div className="mb-6">
        <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
          ← Back to Dashboard
        </Link>
      </div>

      <h1 className="text-2xl font-bold text-gray-900 mb-6">Legal Jurisdictions</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Jurisdiction List */}
        <div className="lg:col-span-2">
          <div className="bg-white shadow rounded-lg overflow-hidden">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Code</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Region</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Legal System</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Currency</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {jurisdictions.map((j) => (
                  <tr key={j.id} className={selected?.id === j.id ? "bg-blue-50" : ""}>
                    <td className="px-6 py-4 text-sm font-mono font-bold text-gray-900">{j.code}</td>
                    <td className="px-6 py-4 text-sm text-gray-900">{j.name}</td>
                    <td className="px-6 py-4">
                      <span className={`px-2 py-1 text-xs font-medium rounded-full ${regionColors[j.region || ""] || "bg-gray-100 text-gray-800"}`}>
                        {j.region || "—"}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-900">{j.legal_system || "—"}</td>
                    <td className="px-6 py-4 text-sm text-gray-900">{j.currency}</td>
                    <td className="px-6 py-4">
                      <button
                        onClick={() => setSelected(j)}
                        className="text-blue-600 hover:text-blue-800 text-sm"
                      >
                        View Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Jurisdiction Detail */}
        <div>
          <div className="bg-white shadow rounded-lg p-6 sticky top-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">
              {selected ? `${selected.name} (${selected.code})` : "Select a Jurisdiction"}
            </h2>

            {selected ? (
              <div className="space-y-4">
                <div>
                  <div className="text-xs text-gray-500 uppercase">Region</div>
                  <div className="text-sm text-gray-900">{selected.region || "Not specified"}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 uppercase">Language</div>
                  <div className="text-sm text-gray-900">{selected.language}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 uppercase">Legal System</div>
                  <div className="text-sm text-gray-900">{selected.legal_system || "Not specified"}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 uppercase">Currency</div>
                  <div className="text-sm text-gray-900">{selected.currency}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 uppercase">Timezone</div>
                  <div className="text-sm text-gray-900">{selected.timezone}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 uppercase">Default Dispute Resolution</div>
                  <div className="text-sm text-gray-900">{selected.default_dispute_resolution || "Not specified"}</div>
                </div>

                {selected.required_clauses && selected.required_clauses.length > 0 && (
                  <div>
                    <div className="text-xs text-gray-500 uppercase mb-1">Required Clauses</div>
                    <div className="flex flex-wrap gap-1">
                      {selected.required_clauses.map((c) => (
                        <span key={c} className="px-2 py-1 text-xs bg-red-100 text-red-800 rounded">{c}</span>
                      ))}
                    </div>
                  </div>
                )}

                {selected.signature_requirements && (
                  <div>
                    <div className="text-xs text-gray-500 uppercase mb-1">Signature Requirements</div>
                    <div className="bg-gray-50 rounded p-3 text-sm">
                      {Object.entries(selected.signature_requirements).map(([key, value]) => (
                        <div key={key} className="flex justify-between py-1">
                          <span className="text-gray-500">{key}:</span>
                          <span className="text-gray-900">{String(value)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <Link
                  href={`/clause-suggestions?jurisdiction=${selected.code}`}
                  className="block w-full text-center px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700"
                >
                  View Clause Suggestions
                </Link>
              </div>
            ) : (
              <p className="text-sm text-gray-500">Click a jurisdiction to view details</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
