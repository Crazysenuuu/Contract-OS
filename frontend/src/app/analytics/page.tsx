"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { listAgreements } from "@/lib/api";

interface Agreement {
  id: string;
  title: string;
  status: string;
  created_at: string;
}

interface StatusCount {
  status: string;
  count: number;
}

interface MonthlyCount {
  month: string;
  count: number;
}

export default function AnalyticsPage() {
  const { token } = useAuth();
  const [agreements, setAgreements] = useState<Agreement[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (token) {
      listAgreements(token)
        .then(setAgreements)
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token]);

  // Calculate metrics
  const statusCounts: StatusCount[] = Object.entries(
    agreements.reduce(
      (acc, a) => {
        acc[a.status] = (acc[a.status] || 0) + 1;
        return acc;
      },
      {} as Record<string, number>
    )
  ).map(([status, count]) => ({ status, count }));

  const totalAgreements = agreements.length;

  const executedCount = agreements.filter(
    (a) => a.status === "executed"
  ).length;

  const pendingCount = agreements.filter(
    (a) => a.status === "draft" || a.status === "internal_review" || a.status === "sent"
  ).length;

  const executionRate =
    totalAgreements > 0
      ? Math.round((executedCount / totalAgreements) * 100)
      : 0;

  // Monthly breakdown (last 6 months)
  const monthlyData: MonthlyCount[] = [];
  const now = new Date();
  for (let i = 5; i >= 0; i--) {
    const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const monthStr = date.toLocaleString("default", {
      month: "short",
      year: "2-digit",
    });
    const count = agreements.filter((a) => {
      const created = new Date(a.created_at);
      return (
        created.getMonth() === date.getMonth() &&
        created.getFullYear() === date.getFullYear()
      );
    }).length;
    monthlyData.push({ month: monthStr, count });
  }

  const maxMonthly = Math.max(...monthlyData.map((m) => m.count), 1);

  // Status colors
  const statusColorMap: Record<string, string> = {
    draft: "bg-gray-500",
    internal_review: "bg-blue-500",
    sent: "bg-purple-500",
    viewed: "bg-indigo-500",
    negotiation: "bg-yellow-500",
    approved: "bg-green-500",
    signing: "bg-orange-500",
    executed: "bg-green-600",
    terminated: "bg-red-500",
  };

  const handleExport = (type: string, format: string = "csv") => {
    if (!token) return;

    const API_BASE = "/api/v1";
    let url = `${API_BASE}/analytics/export/${type}`;
    let filename = `${type}_export.csv`;

    if (format === "pdf") {
      url = `${API_BASE}/analytics/export/${type}-pdf`;
      filename = `${type}_report.pdf`;
    }

    fetch(url, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
      .then((response) => {
        if (!response.ok) throw new Error("Export failed");
        return response.blob();
      })
      .then((blob) => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
      })
      .catch(console.error);
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          href="/dashboard"
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          ← Back to Dashboard
        </Link>
      </div>

      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-gray-900">
          Contract Analytics
        </h1>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => handleExport("analytics", "pdf")}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700"
          >
            📄 PDF Report
          </button>
          <button
            onClick={() => handleExport("compliance", "pdf")}
            className="px-4 py-2 text-sm font-medium text-white bg-red-500 rounded-md hover:bg-red-600"
          >
            📄 Compliance PDF
          </button>
          <button
            onClick={() => handleExport("agreements")}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            📥 Agreements CSV
          </button>
          <button
            onClick={() => handleExport("compliance")}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            📥 Compliance CSV
          </button>
          <button
            onClick={() => handleExport("violations")}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            📥 Violations CSV
          </button>
          <button
            onClick={() => handleExport("obligations")}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            📥 Obligations CSV
          </button>
        </div>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
        <div className="bg-white shadow rounded-lg p-6">
          <div className="text-sm text-gray-500">Total Agreements</div>
          <div className="text-3xl font-bold text-gray-900 mt-1">
            {totalAgreements}
          </div>
        </div>

        <div className="bg-white shadow rounded-lg p-6">
          <div className="text-sm text-gray-500">Executed</div>
          <div className="text-3xl font-bold text-green-600 mt-1">
            {executedCount}
          </div>
        </div>

        <div className="bg-white shadow rounded-lg p-6">
          <div className="text-sm text-gray-500">In Progress</div>
          <div className="text-3xl font-bold text-blue-600 mt-1">
            {pendingCount}
          </div>
        </div>

        <div className="bg-white shadow rounded-lg p-6">
          <div className="text-sm text-gray-500">Execution Rate</div>
          <div className="text-3xl font-bold text-purple-600 mt-1">
            {executionRate}%
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Status Breakdown */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Status Breakdown
          </h2>

          {statusCounts.length === 0 ? (
            <p className="text-sm text-gray-500">No data available</p>
          ) : (
            <div className="space-y-3">
              {statusCounts.map((item) => (
                <div key={item.status} className="flex items-center">
                  <div className="w-24 text-sm text-gray-700 capitalize">
                    {item.status.replace("_", " ")}
                  </div>
                  <div className="flex-1 mx-4">
                    <div className="h-6 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          statusColorMap[item.status] || "bg-gray-400"
                        }`}
                        style={{
                          width: `${
                            totalAgreements > 0
                              ? (item.count / totalAgreements) * 100
                              : 0
                          }%`,
                        }}
                      />
                    </div>
                  </div>
                  <div className="w-12 text-right text-sm font-medium text-gray-900">
                    {item.count}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Monthly Trend */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Monthly Trend
          </h2>

          <div className="flex items-end space-x-2 h-40">
            {monthlyData.map((item) => (
              <div
                key={item.month}
                className="flex-1 flex flex-col items-center"
              >
                <div className="text-xs text-gray-500 mb-1">{item.count}</div>
                <div
                  className="w-full bg-blue-500 rounded-t"
                  style={{
                    height: `${(item.count / maxMonthly) * 100}%`,
                    minHeight: item.count > 0 ? "4px" : "0",
                  }}
                />
                <div className="text-xs text-gray-500 mt-2">{item.month}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Recent Agreements */}
      <div className="mt-6 bg-white shadow rounded-lg p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4">
          Recent Agreements
        </h2>

        {agreements.length === 0 ? (
          <p className="text-sm text-gray-500">No agreements yet</p>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Title
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Created
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {agreements.slice(0, 10).map((agreement) => (
                <tr key={agreement.id}>
                  <td className="px-6 py-4 text-sm text-gray-900">
                    {agreement.title}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`px-2 py-1 text-xs font-medium rounded-full ${
                        agreement.status === "executed"
                          ? "bg-green-100 text-green-800"
                          : agreement.status === "draft"
                          ? "bg-gray-100 text-gray-800"
                          : "bg-blue-100 text-blue-800"
                      }`}
                    >
                      {agreement.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500">
                    {new Date(agreement.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-6 py-4">
                    <Link
                      href={`/agreements/${agreement.id}`}
                      className="text-blue-600 hover:text-blue-800 text-sm"
                    >
                      View
                    </Link>
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
