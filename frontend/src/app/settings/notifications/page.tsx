"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  getNotificationPreferences,
  updateNotificationPreferences,
  resetNotificationPreferences,
  getDeliveryStats,
  listNotifications,
} from "@/lib/api";

interface Preferences {
  id: string;
  email_review_invitation: boolean;
  email_agreement_viewed: boolean;
  email_change_requested: boolean;
  email_agreement_accepted: boolean;
  email_signature_completed: boolean;
  email_approval_request: boolean;
  email_workflow_transition: boolean;
  email_compliance_violation: boolean;
  email_obligation_reminder: boolean;
  digest_enabled: boolean;
  digest_frequency: string;
}

interface DeliveryStats {
  total_sent: number;
  total_failed: number;
  total_pending: number;
  delivery_rate: number;
  by_type: Record<string, Record<string, number>>;
}

interface NotificationRecord {
  id: string;
  notification_type: string;
  to_email: string;
  subject: string;
  status: string;
  message_id: string | null;
  sent_at: string | null;
  created_at: string;
}

const notificationTypes = [
  {
    key: "email_review_invitation",
    label: "Review Invitations",
    description: "When someone is invited to review an agreement",
  },
  {
    key: "email_agreement_viewed",
    label: "Agreement Viewed",
    description: "When a counterparty opens and views an agreement",
  },
  {
    key: "email_change_requested",
    label: "Changes Requested",
    description: "When a counterparty requests changes to an agreement",
  },
  {
    key: "email_agreement_accepted",
    label: "Agreement Accepted",
    description: "When a counterparty accepts an agreement",
  },
  {
    key: "email_signature_completed",
    label: "Signature Completed",
    description: "When someone signs an agreement",
  },
  {
    key: "email_approval_request",
    label: "Approval Requests",
    description: "When an agreement needs your approval",
  },
  {
    key: "email_workflow_transition",
    label: "Status Changes",
    description: "When an agreement moves between workflow states",
  },
  {
    key: "email_compliance_violation",
    label: "Compliance Violations",
    description: "When policy violations are detected",
  },
  {
    key: "email_obligation_reminder",
    label: "Obligation Reminders",
    description: "Reminders for upcoming or overdue obligations",
  },
] as const;

const statusColors: Record<string, string> = {
  sent: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  pending: "bg-yellow-100 text-yellow-800",
};

const typeLabels: Record<string, string> = {
  review_invitation: "Review Invitation",
  agreement_viewed: "Viewed",
  change_requested: "Change Request",
  agreement_accepted: "Accepted",
  signature_completed: "Signed",
  approval_request: "Approval",
  workflow_transition: "Status Change",
  compliance_violation: "Compliance",
  obligation_reminder: "Obligation",
};

export default function NotificationSettingsPage() {
  const { token } = useAuth();
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [deliveryStats, setDeliveryStats] = useState<DeliveryStats | null>(null);
  const [notifications, setNotifications] = useState<NotificationRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [activeTab, setActiveTab] = useState<"preferences" | "delivery">("preferences");

  useEffect(() => {
    if (token) {
      Promise.all([
        getNotificationPreferences(token).catch(() => null),
        getDeliveryStats(token).catch(() => null),
        listNotifications(token, { limit: 20 }).catch(() => []),
      ])
        .then(([prefs, stats, notifs]) => {
          setPreferences(prefs);
          setDeliveryStats(stats);
          setNotifications(notifs);
        })
        .finally(() => setLoading(false));
    }
  }, [token]);

  const handleToggle = async (key: keyof Preferences) => {
    if (!token || !preferences) return;

    const newValue = !preferences[key];
    setPreferences({ ...preferences, [key]: newValue });
    setSaving(true);
    setMessage("");

    try {
      await updateNotificationPreferences(token, { [key]: newValue });
      setMessage("Preferences saved");
      setTimeout(() => setMessage(""), 2000);
    } catch {
      setPreferences({ ...preferences, [key]: !newValue });
    } finally {
      setSaving(false);
    }
  };

  const handleDigestChange = async (frequency: string) => {
    if (!token || !preferences) return;

    setPreferences({ ...preferences, digest_frequency: frequency });
    setSaving(true);

    try {
      await updateNotificationPreferences(token, { digest_frequency: frequency });
      setMessage("Digest frequency updated");
      setTimeout(() => setMessage(""), 2000);
    } catch {
      console.error("Failed");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!token) return;

    setSaving(true);
    try {
      const data = await resetNotificationPreferences(token);
      setPreferences(data as unknown as Preferences);
      setMessage("Preferences reset to defaults");
      setTimeout(() => setMessage(""), 2000);
    } catch {
      console.error("Failed");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  if (!preferences) {
    return (
      <div className="text-center py-12 text-gray-500">Failed to load preferences</div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
          ← Back to Dashboard
        </Link>
      </div>

      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Notification Settings</h1>
        <div className="flex items-center space-x-3">
          {message && (
            <span className="text-sm text-green-600">✓ {message}</span>
          )}
          <button
            onClick={handleReset}
            disabled={saving}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
          >
            Reset to Defaults
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex space-x-1 mb-6 border-b border-gray-200">
        <button
          onClick={() => setActiveTab("preferences")}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === "preferences"
              ? "border-blue-500 text-blue-600"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          ⚙️ Preferences
        </button>
        <button
          onClick={() => setActiveTab("delivery")}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === "delivery"
              ? "border-blue-500 text-blue-600"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          📊 Delivery Status
        </button>
      </div>

      {activeTab === "preferences" && (
        <>
          {/* Email Notifications */}
          <div className="bg-white shadow rounded-lg p-6 mb-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">📧 Email Notifications</h2>
            <div className="divide-y divide-gray-200">
              {notificationTypes.map((type) => (
                <div key={type.key} className="py-4 flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium text-gray-900">{type.label}</div>
                    <div className="text-sm text-gray-500">{type.description}</div>
                  </div>
                  <button
                    onClick={() => handleToggle(type.key)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
                      preferences[type.key as keyof Preferences] ? "bg-blue-600" : "bg-gray-200"
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                        preferences[type.key as keyof Preferences] ? "translate-x-5" : "translate-x-0"
                      }`}
                    />
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Digest Settings */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">📬 Email Digest</h2>
            <div className="py-4 flex items-center justify-between mb-4">
              <div>
                <div className="text-sm font-medium text-gray-900">Enable Digest Mode</div>
                <div className="text-sm text-gray-500">Combine notifications into a single digest email</div>
              </div>
              <button
                onClick={() => handleToggle("digest_enabled" as keyof Preferences)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
                  preferences.digest_enabled ? "bg-blue-600" : "bg-gray-200"
                }`}
              >
                <span
                  className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                    preferences.digest_enabled ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
            {preferences.digest_enabled && (
              <div className="flex space-x-4">
                <button
                  onClick={() => handleDigestChange("daily")}
                  className={`px-4 py-2 text-sm font-medium rounded-md ${
                    preferences.digest_frequency === "daily"
                      ? "bg-blue-600 text-white"
                      : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                  }`}
                >
                  Daily
                </button>
                <button
                  onClick={() => handleDigestChange("weekly")}
                  className={`px-4 py-2 text-sm font-medium rounded-md ${
                    preferences.digest_frequency === "weekly"
                      ? "bg-blue-600 text-white"
                      : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                  }`}
                >
                  Weekly
                </button>
              </div>
            )}
          </div>
        </>
      )}

      {activeTab === "delivery" && (
        <>
          {/* Delivery Stats */}
          {deliveryStats && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <div className="bg-white shadow rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-green-600">{deliveryStats.total_sent}</div>
                <div className="text-xs text-gray-500">Sent</div>
              </div>
              <div className="bg-white shadow rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-red-600">{deliveryStats.total_failed}</div>
                <div className="text-xs text-gray-500">Failed</div>
              </div>
              <div className="bg-white shadow rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-yellow-600">{deliveryStats.total_pending}</div>
                <div className="text-xs text-gray-500">Pending</div>
              </div>
              <div className="bg-white shadow rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-blue-600">{deliveryStats.delivery_rate}%</div>
                <div className="text-xs text-gray-500">Delivery Rate</div>
              </div>
            </div>
          )}

          {/* By Type Stats */}
          {deliveryStats && Object.keys(deliveryStats.by_type).length > 0 && (
            <div className="bg-white shadow rounded-lg p-6 mb-6">
              <h2 className="text-lg font-medium text-gray-900 mb-4">By Notification Type</h2>
              <div className="space-y-3">
                {Object.entries(deliveryStats.by_type).map(([type, counts]) => (
                  <div key={type} className="flex items-center justify-between py-2 border-b border-gray-100">
                    <div className="text-sm font-medium text-gray-700">
                      {typeLabels[type] || type}
                    </div>
                    <div className="flex space-x-4 text-sm">
                      <span className="text-green-600">✓ {counts.sent || 0}</span>
                      <span className="text-red-600">✗ {counts.failed || 0}</span>
                      <span className="text-yellow-600">⏳ {counts.pending || 0}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent Notifications */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">Recent Notifications</h2>
            {notifications.length === 0 ? (
              <p className="text-sm text-gray-500">No notifications yet</p>
            ) : (
              <div className="space-y-3">
                {notifications.map((notif) => (
                  <div
                    key={notif.id}
                    className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0"
                  >
                    <div className="flex-1">
                      <div className="text-sm font-medium text-gray-900">{notif.subject}</div>
                      <div className="text-xs text-gray-500">
                        To: {notif.to_email} • {typeLabels[notif.notification_type] || notif.notification_type}
                      </div>
                    </div>
                    <div className="flex items-center space-x-3">
                      <span className={`px-2 py-1 text-xs font-medium rounded-full ${statusColors[notif.status] || "bg-gray-100"}`}>
                        {notif.status}
                      </span>
                      <span className="text-xs text-gray-400">
                        {notif.sent_at ? new Date(notif.sent_at).toLocaleString() : "—"}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
