"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  getExecutiveAnalytics,
  getFinancialAnalytics,
  getComplianceSummary,
  type ExecutiveAnalytics,
  type FinancialAnalytics,
  type ComplianceSummary,
} from "@/lib/api";

const statusColorMap: Record<string, string> = {
  draft: "bg-gray-500",
  internal_review: "bg-blue-500",
  pending_approval: "bg-blue-500",
  approved: "bg-green-500",
  sent: "bg-purple-500",
  viewed: "bg-indigo-500",
  negotiation: "bg-yellow-500",
  negotiating: "bg-yellow-500",
  ready_for_signature: "bg-orange-500",
  signing: "bg-orange-500",
  partially_signed: "bg-amber-500",
  executed: "bg-green-600",
  active: "bg-emerald-500",
  expiring: "bg-amber-500",
  expired: "bg-gray-400",
  terminated: "bg-red-500",
  renewed: "bg-teal-500",
  superseded: "bg-slate-500",
  cancelled: "bg-red-500",
};

function StatCard({
  label,
  value,
  sub,
  color = "text-gray-900",
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="bg-white shadow rounded-lg p-6">
      <div className="text-sm text-gray-500">{label}</div>
      <div className={`text-3xl font-bold mt-1 ${color}`}>{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-1">{sub}</div>}
    </div>
  );
}

function BarChart({
  data,
  maxVal,
}: {
  data: { label: string; value: number }[];
  maxVal: number;
}) {
  return (
    <div className="flex items-end space-x-2 h-40">
      {data.map((item) => (
        <div key={item.label} className="flex-1 flex flex-col items-center">
          <div className="text-xs text-gray-500 mb-1">{item.value}</div>
          <div
            className="w-full bg-blue-500 rounded-t"
            style={{
              height: `${maxVal > 0 ? (item.value / maxVal) * 100 : 0}%`,
              minHeight: item.value > 0 ? "4px" : "0",
            }}
          />
          <div className="text-xs text-gray-500 mt-2 text-center truncate w-full">
            {item.label}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function AnalyticsPage() {
  const { token } = useAuth();
  const [executive, setExecutive] = useState<ExecutiveAnalytics | null>(null);
  const [financial, setFinancial] = useState<FinancialAnalytics | null>(null);
  const [compliance, setCompliance] = useState<ComplianceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    Promise.all([
      getExecutiveAnalytics(token).catch(() => null),
      getFinancialAnalytics(token).catch(() => null),
      getComplianceSummary(token).catch(() => null),
    ])
      .then(([e, f, c]) => {
        setExecutive(e);
        setFinancial(f);
        setCompliance(c);
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading analytics…</div>;
  }

  if (error) {
    return (
      <div className="text-center py-12 text-red-500">
        Failed to load analytics: {error}
      </div>
    );
  }

  const vol = executive?.volume;
  const monthlyTrend = vol?.monthly_trend ?? [];
  const maxMonthly = Math.max(...monthlyTrend.map((m) => m.count), 1);

  const statusEntries = vol
    ? Object.entries(vol.by_status).sort((a, b) => b[1] - a[1])
    : [];

  return (
    <div>
      <div className="mb-6">
        <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
          ← Back to Dashboard
        </Link>
      </div>

      <h1 className="text-2xl font-bold text-gray-900 mb-6">
        Contract Analytics
      </h1>

      {/* ─── Executive KPI Cards ─── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-8">
        <StatCard label="Total Contracts" value={vol?.total ?? 0} />
        <StatCard
          label="Execution Rate"
          value={`${executive?.rates.execution_rate ?? 0}%`}
          color="text-green-600"
        />
        <StatCard
          label="Avg Cycle Time"
          value={`${executive?.cycle_time.average_days ?? 0}d`}
          sub={`Median ${executive?.cycle_time.median_days ?? 0}d · n=${executive?.cycle_time.sample_size ?? 0}`}
        />
        <StatCard
          label="Avg Approval Time"
          value={`${executive?.approval_time.average_days ?? 0}d`}
          sub={`n=${executive?.approval_time.sample_size ?? 0}`}
        />
      </div>

      {/* ─── Rates ─── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-8">
        <StatCard
          label="Renewal Rate"
          value={`${executive?.rates.renewal_rate ?? 0}%`}
          color="text-blue-600"
        />
        <StatCard
          label="Termination Rate"
          value={`${executive?.rates.termination_rate ?? 0}%`}
          color="text-red-600"
        />
        <StatCard
          label="Obligation Compliance"
          value={`${executive?.rates.obligation_compliance_rate ?? 0}%`}
          color="text-emerald-600"
        />
        <StatCard
          label="Overdue Obligations"
          value={executive?.obligations.overdue ?? 0}
          sub={`${executive?.obligations.completed ?? 0} completed of ${executive?.obligations.total ?? 0}`}
          color="text-amber-600"
        />
      </div>

      {/* ─── Financial ─── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-8">
        <StatCard
          label="Total Contract Value"
          value={`$${(financial?.total_contract_value ?? 0).toLocaleString()}`}
          color="text-indigo-600"
        />
        <StatCard
          label="Committed Spend"
          value={`$${(financial?.committed_spend ?? 0).toLocaleString()}`}
          color="text-violet-600"
        />
        <StatCard
          label="Outstanding Obligations"
          value={`$${(financial?.outstanding_obligations ?? 0).toLocaleString()}`}
        />
        <StatCard
          label="Overdue Obligations"
          value={`$${(financial?.overdue_obligations ?? 0).toLocaleString()}`}
          color="text-red-600"
        />
      </div>

      {/* ─── Compliance Summary (spec §89) ─── */}
      {compliance && (
        <div className="mb-8 bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Compliance Overview
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            {[
              { label: "Requiring Review", value: compliance.contracts_requiring_review, color: "text-amber-600" },
              { label: "Missing DPA", value: compliance.missing_dpa, color: "text-red-600" },
              { label: "Unsigned Amendments", value: compliance.unsigned_amendments, color: "text-orange-600" },
              { label: "Upcoming Renewals", value: compliance.upcoming_renewals, color: "text-blue-600" },
              { label: "Pending Approvals", value: compliance.pending_approvals, color: "text-purple-600" },
              { label: "Overdue Obligations", value: compliance.overdue_obligations, color: "text-red-600" },
            ].map((item) => (
              <div key={item.label} className="text-center">
                <div className={`text-2xl font-bold ${item.color}`}>{item.value}</div>
                <div className="text-xs text-gray-500 mt-1">{item.label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* ─── Status Breakdown ─── */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Status Breakdown
          </h2>
          {statusEntries.length === 0 ? (
            <p className="text-sm text-gray-500">No data available</p>
          ) : (
            <div className="space-y-3">
              {statusEntries.map(([status, count]) => (
                <div key={status} className="flex items-center">
                  <div className="w-32 text-sm text-gray-700 capitalize">
                    {status.replace(/_/g, " ")}
                  </div>
                  <div className="flex-1 mx-4">
                    <div className="h-6 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${statusColorMap[status] || "bg-gray-400"}`}
                        style={{
                          width: `${vol && vol.total > 0 ? (count / vol.total) * 100 : 0}%`,
                        }}
                      />
                    </div>
                  </div>
                  <div className="w-12 text-right text-sm font-medium text-gray-900">
                    {count}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ─── Monthly Trend ─── */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Monthly Creation Trend
          </h2>
          {monthlyTrend.length === 0 ? (
            <p className="text-sm text-gray-500">No data available</p>
          ) : (
            <BarChart
              data={monthlyTrend.map((m) => ({
                label: new Date(m.month).toLocaleString("default", { month: "short", year: "2-digit" }),
                value: m.count,
              }))}
              maxVal={maxMonthly}
            />
          )}
        </div>
      </div>

      {/* ─── Risk Distribution ─── */}
      {executive?.risk && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">
              Risk Distribution
            </h2>
            <div className="grid grid-cols-4 gap-4">
              {[
                { label: "Low", value: executive.risk.low, color: "text-green-600" },
                { label: "Medium", value: executive.risk.medium, color: "text-amber-600" },
                { label: "High", value: executive.risk.high, color: "text-orange-600" },
                { label: "Critical", value: executive.risk.critical, color: "text-red-600" },
              ].map((r) => (
                <div key={r.label} className="text-center p-4 bg-gray-50 rounded-lg">
                  <div className={`text-3xl font-bold ${r.color}`}>{r.value}</div>
                  <div className="text-sm text-gray-500 mt-1">{r.label}</div>
                </div>
              ))}
            </div>
          </div>

          {/* ─── Value by Status ─── */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">
              Contract Value by Status
            </h2>
            {financial?.value_by_status &&
              Object.entries(financial.value_by_status).length > 0 ? (
              <div className="space-y-3">
                {Object.entries(financial.value_by_status)
                  .sort((a, b) => b[1].total_value - a[1].total_value)
                  .map(([status, data]) => (
                    <div key={status} className="flex items-center">
                      <div className="w-32 text-sm text-gray-700 capitalize">
                        {status.replace(/_/g, " ")}
                      </div>
                      <div className="flex-1 mx-4">
                        <div className="h-6 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${statusColorMap[status] || "bg-gray-400"}`}
                            style={{
                              width: `${
                                financial.total_contract_value > 0
                                  ? (data.total_value / financial.total_contract_value) * 100
                                  : 0
                              }%`,
                            }}
                          />
                        </div>
                      </div>
                      <div className="w-24 text-right text-sm font-medium text-gray-900">
                        ${data.total_value.toLocaleString()}
                      </div>
                    </div>
                  ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">No value data available</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
