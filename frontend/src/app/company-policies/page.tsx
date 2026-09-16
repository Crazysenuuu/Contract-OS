"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { useRouter } from "next/navigation";
import {
  listCompanyPolicies,
  getPolicySummary,
} from "@/lib/api";

export default function CompanyPoliciesPage() {
  const { token } = useAuth();
  const router = useRouter();
  const [policies, setPolicies] = useState<Array<{id: string; name: string; description: string; action: string; approval_roles: string[]; severity: string}>>([]);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<{
    total_rules: number;
    rules_by_severity: Record<string, number>;
    approval_roles_required: string[];
  } | null>(null);

  useEffect(() => {
    if (!token) router.push("/login");
  }, [token, router]);

  // Declared before the effect that calls it; no leading setLoading(true)
  // so the mount effect never cascades synchronously.
  const loadPolicies = useCallback(async () => {
    if (!token) return;
    try {
      const [policiesData, summaryData] = await Promise.all([
        listCompanyPolicies(token),
        getPolicySummary(token),
      ]);
      setPolicies(policiesData.rules ?? []);
      setSummary(summaryData);
    } catch (err) {
      console.error("Failed to load policies:", err);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (token) queueMicrotask(() => loadPolicies());
  }, [token, loadPolicies]);

  const severityColors: Record<string, string> = {
    info: "bg-blue-100 text-blue-800",
    warning: "bg-yellow-100 text-yellow-800",
    critical: "bg-red-100 text-red-800",
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Company Policies</h1>
          <p className="text-gray-500 mt-1">
            Manage contract compliance policies and rules
          </p>
        </div>
        <button
          onClick={() => router.push("/dashboard")}
          className="text-blue-600 hover:underline"
        >
          ← Back
        </button>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-3 gap-4 mb-8">
          <div className="bg-white rounded-xl border p-5">
            <div className="text-3xl font-bold text-blue-600">
              {summary.total_rules}
            </div>
            <div className="text-sm text-gray-500 mt-1">Total Rules</div>
          </div>
          <div className="bg-white rounded-xl border p-5">
            <div className="text-3xl font-bold text-yellow-600">
              {Object.values(summary.rules_by_severity).reduce((a, b) => a + b, 0)}
            </div>
            <div className="text-sm text-gray-500 mt-1">By Severity</div>
          </div>
          <div className="bg-white rounded-xl border p-5">
            <div className="text-3xl font-bold text-purple-600">
              {summary.approval_roles_required.length}
            </div>
            <div className="text-sm text-gray-500 mt-1">Approval Roles</div>
          </div>
        </div>
      )}

      {/* Policies List */}
      {policies.length === 0 ? (
        <div className="text-center py-16 bg-gray-50 rounded-xl">
          <span className="text-4xl">📋</span>
          <p className="text-gray-500 mt-4">No policies configured</p>
          <p className="text-sm text-gray-400 mt-1">
            Policies help enforce compliance rules across your agreements
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border">
          <div className="p-4 border-b">
            <h2 className="font-semibold">Active Policies ({policies.length})</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left py-3 px-4">Policy Name</th>
                  <th className="text-left py-3 px-4">Action</th>
                  <th className="text-left py-3 px-4">Severity</th>
                  <th className="text-left py-3 px-4">Approval Roles</th>
                </tr>
              </thead>
              <tbody>
                {policies.map((policy) => (
                  <tr key={policy.id} className="border-b hover:bg-gray-50">
                    <td className="py-3 px-4">
                      <div className="font-medium">{policy.name}</div>
                      <div className="text-gray-500 text-xs mt-0.5">
                        {policy.description}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <span className="px-2 py-1 bg-gray-100 rounded text-xs">
                        {policy.action}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`px-2 py-1 rounded text-xs ${
                          severityColors[policy.severity] || ""
                        }`}
                      >
                        {policy.severity}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex gap-1 flex-wrap">
                        {policy.approval_roles.map((role) => (
                          <span
                            key={role}
                            className="px-2 py-0.5 bg-purple-100 text-purple-700 rounded text-xs"
                          >
                            {role}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
