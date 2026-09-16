"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { apiClient } from "@/lib/api";

interface AlertingPreferences {
  slack_enabled: boolean;
  pagerduty_enabled: boolean;
  email_enabled: boolean;
  compliance_alerts: boolean;
  esignature_alerts: boolean;
  migration_alerts: boolean;
  quiet_hours_start: number | null;
  quiet_hours_end: number | null;
}

interface EscalationPolicy {
  id: string;
  name: string;
  category: string;
  enabled: boolean;
  min_severity: string;
  cooldown_seconds: number;
  tiers: Array<{
    level: number;
    delay_seconds: number;
    channels: string[];
    notify_roles: string[];
    slack_channel: string | null;
  }>;
}

interface AlertStats {
  total_alerts: number;
  last_24h: number;
  by_severity: Record<string, number>;
  providers: string[];
  enabled: boolean;
}

interface AlertHistoryEntry {
  title: string;
  message: string;
  severity: string;
  category: string;
  timestamp: string;
  provider_results: Record<string, boolean>;
}

const channelIcons: Record<string, string> = {
  slack: "💬",
  pagerduty: "📟",
  email: "📧",
};

const severityColors: Record<string, string> = {
  info: "text-blue-600 bg-blue-50",
  warning: "text-amber-600 bg-amber-50",
  critical: "text-red-600 bg-red-50",
};

export default function AlertingSettingsPage() {
  const { user, token } = useAuth();

  // ── State ────────────────────────────────────────────────
  const [preferences, setPreferences] = useState<AlertingPreferences | null>(null);
  const [policies, setPolicies] = useState<EscalationPolicy[]>([]);
  const [alertStats, setAlertStats] = useState<AlertStats | null>(null);
  const [alertHistory, setAlertHistory] = useState<AlertHistoryEntry[]>([]);
  const [providers, setProviders] = useState<Array<{ name: string; configured: boolean }>>([]);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingProvider, setTestingProvider] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"preferences" | "policies" | "history">("preferences");

  // ── Fetch data ───────────────────────────────────────────
  useEffect(() => {
    if (!token) return;
    fetchData();
  }, [token]);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      const headers = { Authorization: `Bearer ${token}` };

      const [prefRes, polRes, statsRes, histRes, provRes] = await Promise.allSettled([
        fetch(`${apiClient.baseUrl}/api/v1/escalation/preferences`, { headers }),
        fetch(`${apiClient.baseUrl}/api/v1/escalation/policies`, { headers }),
        fetch(`${apiClient.baseUrl}/api/v1/alerting/stats`, { headers }),
        fetch(`${apiClient.baseUrl}/api/v1/alerting/history?limit=20`, { headers }),
        fetch(`${apiClient.baseUrl}/api/v1/alerting/providers`, { headers }),
      ]);

      if (prefRes.status === "fulfilled" && prefRes.value.ok) {
        const data = await prefRes.value.json();
        setPreferences(data.preferences);
      }
      if (polRes.status === "fulfilled" && polRes.value.ok) {
        const data = await polRes.value.json();
        setPolicies(data.policies || []);
      }
      if (statsRes.status === "fulfilled" && statsRes.value.ok) {
        const data = await statsRes.value.json();
        setAlertStats(data);
      }
      if (histRes.status === "fulfilled" && histRes.value.ok) {
        const data = await histRes.value.json();
        setAlertHistory(data);
      }
      if (provRes.status === "fulfilled" && provRes.value.ok) {
        const data = await provRes.value.json();
        setProviders(data);
      }
    } catch {
      setError("Failed to load alerting settings");
    } finally {
      setLoading(false);
    }
  }

  // ── Save preferences ─────────────────────────────────────
  async function savePreferences() {
    if (!preferences || !token) return;
    setSaving(true);
    try {
      const res = await fetch(`${apiClient.baseUrl}/api/v1/escalation/preferences`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(preferences),
      });
      if (res.ok) {
        setTestResult("Preferences saved");
        setTimeout(() => setTestResult(null), 3000);
      }
    } finally {
      setSaving(false);
    }
  }

  // ── Test alert ───────────────────────────────────────────
  async function testAlert(provider: string) {
    if (!token) return;
    setTestingProvider(provider);
    setTestResult(null);
    try {
      const res = await fetch(`${apiClient.baseUrl}/api/v1/alerting/test`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ provider, severity: "info" }),
      });
      const data = await res.json();
      if (res.ok) {
        setTestResult(`✅ ${data.message}`);
      } else {
        setTestResult(`❌ ${data.detail || "Test failed"}`);
      }
    } catch {
      setTestResult("❌ Network error");
    } finally {
      setTestingProvider(null);
      setTimeout(() => setTestResult(null), 5000);
    }
  }

  // ── Toggle policy ────────────────────────────────────────
  async function togglePolicy(policyId: string, enabled: boolean) {
    if (!token) return;
    try {
      await fetch(`${apiClient.baseUrl}/api/v1/escalation/policies/${policyId}`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      setPolicies(prev => prev.map(p => p.id === policyId ? { ...p, enabled } : p));
    } catch {
      setError("Failed to update policy");
    }
  }

  // ── Render ───────────────────────────────────────────────
  if (loading) {
    return (
      <div className="max-w-4xl mx-auto p-8">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-gray-200 rounded w-64" />
          <div className="h-64 bg-gray-200 rounded" />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto p-8 space-y-8">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 text-sm text-gray-500 mb-2">
          <Link href="/settings" className="hover:text-gray-700">Settings</Link>
          <span>/</span>
          <span>Alerting</span>
        </div>
        <h1 className="text-2xl font-bold text-gray-900">Alerting & Escalation</h1>
        <p className="text-gray-600 mt-1">Configure Slack/PagerDuty alerts and escalation policies</p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">
          {error}
        </div>
      )}

      {testResult && (
        <div className={`px-4 py-3 rounded-lg ${testResult.startsWith("✅") ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
          {testResult}
        </div>
      )}

      {/* Stats cards */}
      {alertStats && (
        <div className="grid grid-cols-4 gap-4">
          <StatCard label="Total Alerts" value={alertStats.total_alerts} icon="🔔" />
          <StatCard label="Last 24h" value={alertStats.last_24h} icon="⏰" />
          <StatCard label="Providers" value={alertStats.providers.length} icon="🔌" />
          <StatCard label="Status" value={alertStats.enabled ? "Active" : "Disabled"} icon={alertStats.enabled ? "🟢" : "🔴"} />
        </div>
      )}

      {/* Provider status */}
      <div className="bg-white rounded-xl border p-6">
        <h2 className="font-semibold text-lg mb-4">Connected Providers</h2>
        <div className="flex gap-4">
          {providers.map(p => (
            <div key={p.name} className="flex items-center gap-3 border rounded-lg px-4 py-3">
              <span className="text-xl">{p.configured ? "🟢" : "⚪"}</span>
              <div>
                <div className="font-medium capitalize">{p.name}</div>
                <div className="text-sm text-gray-500">{p.configured ? "Configured" : "Not configured"}</div>
              </div>
              <button
                onClick={() => testAlert(p.name)}
                disabled={!p.configured || testingProvider === p.name}
                className="ml-4 text-sm px-3 py-1 rounded bg-blue-50 text-blue-600 hover:bg-blue-100 disabled:opacity-40"
              >
                {testingProvider === p.name ? "Testing..." : "Test"}
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b">
        <div className="flex gap-6">
          {(["preferences", "policies", "history"] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab
                  ? "border-blue-500 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {tab === "preferences" ? "Notification Preferences" : tab === "policies" ? "Escalation Policies" : "Alert History"}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      {activeTab === "preferences" && preferences && (
        <div className="bg-white rounded-xl border p-6 space-y-6">
          <h2 className="font-semibold text-lg">Your Notification Channels</h2>

          <div className="space-y-4">
            <ToggleRow
              label="Slack Notifications"
              description="Receive alerts in your Slack workspace"
              icon="💬"
              checked={preferences.slack_enabled}
              onChange={v => setPreferences({ ...preferences, slack_enabled: v })}
            />
            <ToggleRow
              label="PagerDuty Incidents"
              description="Receive PagerDuty incidents for critical alerts"
              icon="📟"
              checked={preferences.pagerduty_enabled}
              onChange={v => setPreferences({ ...preferences, pagerduty_enabled: v })}
            />
            <ToggleRow
              label="Email Notifications"
              description="Receive email summaries of alerts"
              icon="📧"
              checked={preferences.email_enabled}
              onChange={v => setPreferences({ ...preferences, email_enabled: v })}
            />
          </div>

          <hr />
          <h3 className="font-medium">Alert Categories</h3>
          <div className="space-y-4">
            <ToggleRow
              label="Compliance Violations"
              description="Policy deviations and compliance failures"
              icon="📋"
              checked={preferences.compliance_alerts}
              onChange={v => setPreferences({ ...preferences, compliance_alerts: v })}
            />
            <ToggleRow
              label="E-Signature Events"
              description="Signature completions, failures, and execution alerts"
              icon="✍️"
              checked={preferences.esignature_alerts}
              onChange={v => setPreferences({ ...preferences, esignature_alerts: v })}
            />
            <ToggleRow
              label="Migration Alerts"
              description="Database migration failures and rollbacks"
              icon="🗄️"
              checked={preferences.migration_alerts}
              onChange={v => setPreferences({ ...preferences, migration_alerts: v })}
            />
          </div>

          <div className="flex justify-end pt-4">
            <button
              onClick={savePreferences}
              disabled={saving}
              className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save Preferences"}
            </button>
          </div>
        </div>
      )}

      {activeTab === "policies" && (
        <div className="space-y-4">
          {policies.length === 0 ? (
            <div className="bg-white rounded-xl border p-8 text-center text-gray-500">
              No escalation policies configured. Default policies will be created automatically.
            </div>
          ) : (
            policies.map(policy => (
              <div key={policy.id} className="bg-white rounded-xl border p-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="font-semibold text-lg">{policy.name}</h3>
                    <div className="flex gap-2 mt-1">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                        {policy.category}
                      </span>
                      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                        min: {policy.min_severity}
                      </span>
                      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                        cooldown: {policy.cooldown_seconds}s
                      </span>
                    </div>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={policy.enabled}
                      onChange={e => togglePolicy(policy.id, e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:ring-2 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600" />
                  </label>
                </div>

                {/* Tiers */}
                <div className="space-y-3">
                  {policy.tiers
                    .sort((a, b) => a.level - b.level)
                    .map(tier => (
                      <div key={tier.level} className="flex items-center gap-3 bg-gray-50 rounded-lg px-4 py-3">
                        <span className="text-sm font-medium text-gray-500 w-16">Tier {tier.level}</span>
                        <div className="flex gap-2">
                          {tier.channels.map(ch => (
                            <span key={ch} className="text-xs px-2 py-1 rounded bg-white border">
                              {channelIcons[ch] || "📡"} {ch}
                            </span>
                          ))}
                        </div>
                        {tier.delay_seconds > 0 && (
                          <span className="text-xs text-gray-400">after {tier.delay_seconds}s</span>
                        )}
                        {tier.notify_roles.length > 0 && (
                          <span className="text-xs text-gray-500">
                            → {tier.notify_roles.join(", ")}
                          </span>
                        )}
                        {tier.slack_channel && (
                          <span className="text-xs text-gray-400">
                            #{tier.slack_channel.replace("#", "")}
                          </span>
                        )}
                      </div>
                    ))}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {activeTab === "history" && (
        <div className="bg-white rounded-xl border overflow-hidden">
          {alertHistory.length === 0 ? (
            <div className="p-8 text-center text-gray-500">No alerts recorded yet</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="text-left px-4 py-3 font-medium">Severity</th>
                  <th className="text-left px-4 py-3 font-medium">Title</th>
                  <th className="text-left px-4 py-3 font-medium">Category</th>
                  <th className="text-left px-4 py-3 font-medium">Providers</th>
                  <th className="text-left px-4 py-3 font-medium">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {alertHistory.map((alert, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-1 rounded-full ${severityColors[alert.severity] || ""}`}>
                        {alert.severity}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-medium">{alert.title}</td>
                    <td className="px-4 py-3 text-gray-500">{alert.category}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        {Object.entries(alert.provider_results).map(([name, ok]) => (
                          <span key={name} className={`text-xs ${ok ? "text-green-600" : "text-red-600"}`}>
                            {ok ? "✅" : "❌"} {name}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-gray-400 text-xs">
                      {new Date(alert.timestamp).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────

function StatCard({ label, value, icon }: { label: string; value: string | number; icon: string }) {
  return (
    <div className="bg-white rounded-xl border p-4">
      <div className="flex items-center gap-2 text-sm text-gray-500 mb-1">
        <span>{icon}</span> {label}
      </div>
      <div className="text-2xl font-bold text-gray-900">{value}</div>
    </div>
  );
}

function ToggleRow({
  label, description, icon, checked, onChange,
}: {
  label: string; description: string; icon: string; checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-3">
        <span className="text-xl">{icon}</span>
        <div>
          <div className="font-medium text-sm">{label}</div>
          <div className="text-xs text-gray-500">{description}</div>
        </div>
      </div>
      <label className="relative inline-flex items-center cursor-pointer">
        <input
          type="checkbox"
          checked={checked}
          onChange={e => onChange(e.target.checked)}
          className="sr-only peer"
        />
        <div className="w-11 h-6 bg-gray-200 peer-focus:ring-2 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600" />
      </label>
    </div>
  );
}
