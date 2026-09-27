"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import Panel from "@/components/Panel";
import {
  getDashboard,
  getComplianceSummary,
  createMyTask,
  updateMyTask,
  deleteMyTask,
  markAllNotificationsRead,
  DashboardPayload,
  DashboardTask,
  ComplianceSummary,
} from "@/lib/api";

const statusColors: Record<string, string> = {
  pending: "bg-amber-100 text-amber-800 border border-amber-200",
  in_progress: "bg-blue-100 text-blue-800 border border-blue-200",
  completed: "bg-emerald-100 text-emerald-800 border border-emerald-200",
  cancelled: "bg-gray-100 text-gray-600 border border-gray-200",
};

const priorityColors: Record<string, string> = {
  low: "text-gray-500",
  normal: "text-gray-700",
  high: "text-rose-600",
  urgent: "text-rose-700 font-bold",
};

export default function DashboardPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<DashboardPayload | null>(null);
  const [compliance, setCompliance] = useState<ComplianceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [showTaskForm, setShowTaskForm] = useState(false);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDue, setTaskDue] = useState("");
  const [taskPriority, setTaskPriority] = useState("normal");
  const [addingTask, setAddingTask] = useState(false);

  const load = () => {
    if (!token) return;
    getDashboard(token)
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
    getComplianceSummary(token)
      .then(setCompliance)
      .catch(() => {}); // non-blocking
  };

  useEffect(load, [token]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      router.push(`/search?q=${encodeURIComponent(searchQuery.trim())}`);
    }
  };

  const handleAddTask = async () => {
    if (!token || !taskTitle.trim()) return;
    setAddingTask(true);
    try {
      await createMyTask(token, {
        title: taskTitle.trim(),
        priority: taskPriority,
        due_at: taskDue || undefined,
      });
      setTaskTitle("");
      setTaskDue("");
      setTaskPriority("normal");
      setShowTaskForm(false);
      load();
    } catch (err) {
      console.error(err);
    } finally {
      setAddingTask(false);
    }
  };

  const handleToggleTask = async (task: DashboardTask) => {
    if (!token) return;
    const next = task.status === "completed" ? "pending" : "completed";
    await updateMyTask(token, task.id, { status: next });
    load();
  };

  const handleDeleteTask = async (taskId: string) => {
    if (!token) return;
    await deleteMyTask(token, taskId);
    load();
  };

  const handleMarkAllRead = async () => {
    if (!token) return;
    await markAllNotificationsRead(token);
    load();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <div className="flex flex-col items-center gap-3">
          <div className="w-7 h-7 rounded-full border-2 border-indigo-600 border-t-transparent animate-spin" />
          <span className="text-sm text-gray-500">Loading dashboard…</span>
        </div>
      </div>
    );
  }

  const counts = data?.counts || {
    pending_tasks: 0,
    unread_notifications: 0,
    agreements: 0,
    executed_agreements: 0,
    open_obligations: 0,
  };

  const statCards = [
    { label: "Agreements", value: counts.agreements, href: "/agreements", color: "from-blue-500 to-indigo-500" },
    { label: "Executed", value: counts.executed_agreements, href: "/search?status=executed", color: "from-emerald-500 to-teal-500" },
    { label: "My Tasks", value: counts.pending_tasks, href: "/dashboard", color: "from-amber-500 to-orange-500" },
    { label: "Unread Alerts", value: counts.unread_notifications, href: "/outbox", color: "from-rose-500 to-pink-500" },
    { label: "Open Obligations", value: counts.open_obligations, href: "/agreements/obligations", color: "from-purple-500 to-violet-500" },
  ];

  return (
    <div className="space-y-5 max-w-6xl">
      {/* ── Page header + search ─────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Good {timeOfDay()}, {user?.name?.split(" ")[0] || "there"} 👋
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Overview of your contracts, tasks, and activity.
          </p>
        </div>
        <form onSubmit={handleSearch} className="flex items-center gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search agreements…"
            aria-label="Search agreements"
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm w-56 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
          <button
            type="submit"
            className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors"
          >
            Search
          </button>
        </form>
      </div>

      {/* ── Stat cards ───────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {statCards.map((card) => (
          <Link
            key={card.label}
            href={card.href}
            className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 hover:shadow-md transition-all duration-200 hover:-translate-y-0.5 group"
          >
            <div
              className={`w-10 h-10 rounded-xl bg-gradient-to-br ${card.color} flex items-center justify-center text-white text-sm font-bold mb-3 group-hover:scale-105 transition-transform`}
            >
              {card.value}
            </div>
            <div className="text-sm font-medium text-gray-700">{card.label}</div>
          </Link>
        ))}
      </div>

      {/* ── Compliance overview ──────────────────────────── */}
      {compliance && (
        <Panel
          title="Compliance Overview"
          icon="🛡️"
          subtitle="Contracts needing attention"
          defaultCollapsed={false}
        >
          <div className="grid grid-cols-3 md:grid-cols-6 gap-4">
            {(
              [
                { label: "Requiring Review", value: compliance.contracts_requiring_review, color: "text-amber-600", href: "/agreements/compliance" },
                { label: "Missing DPA", value: compliance.missing_dpa, color: "text-red-600", href: "/agreements/compliance" },
                { label: "Unsigned Amendments", value: compliance.unsigned_amendments, color: "text-orange-600", href: "/amendments" },
                { label: "Upcoming Renewals", value: compliance.upcoming_renewals, color: "text-blue-600", href: "/renewals" },
                { label: "Pending Approvals", value: compliance.pending_approvals, color: "text-purple-600", href: "/approvals" },
                { label: "Overdue Obligations", value: compliance.overdue_obligations, color: "text-red-600", href: "/agreements/obligations" },
              ] as const
            ).map((item) => (
              <Link key={item.label} href={item.href} className="text-center hover:opacity-80 transition-opacity">
                <div className={`text-2xl font-bold ${item.color}`}>{item.value}</div>
                <div className="text-xs text-gray-500 mt-0.5 leading-tight">{item.label}</div>
              </Link>
            ))}
          </div>
        </Panel>
      )}

      {/* ── Main 2-col row: Tasks + Notifications ───────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Tasks */}
        <Panel
          title="My Tasks"
          icon="✅"
          subtitle={`${counts.pending_tasks} pending`}
          className="lg:col-span-2"
          defaultCollapsed={false}
          action={
            <button
              onClick={() => setShowTaskForm((v) => !v)}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors px-2 py-1 rounded-md hover:bg-indigo-50"
            >
              {showTaskForm ? "Cancel" : "+ New Task"}
            </button>
          }
        >
          {showTaskForm && (
            <div className="mb-4 p-4 bg-gray-50 rounded-xl space-y-3 border border-gray-100">
              <input
                type="text"
                value={taskTitle}
                onChange={(e) => setTaskTitle(e.target.value)}
                placeholder="Task title"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
              <div className="flex gap-3 flex-wrap">
                <input
                  type="date"
                  value={taskDue}
                  onChange={(e) => setTaskDue(e.target.value)}
                  className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
                />
                <select
                  value={taskPriority}
                  onChange={(e) => setTaskPriority(e.target.value)}
                  className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
                <button
                  onClick={handleAddTask}
                  disabled={addingTask || !taskTitle.trim()}
                  className="px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                >
                  {addingTask ? "Adding…" : "Add"}
                </button>
              </div>
            </div>
          )}

          {!data?.tasks?.length ? (
            <p className="text-sm text-gray-500 py-8 text-center">
              No tasks yet. Add a task to track your to-dos.
            </p>
          ) : (
            <ul className="divide-y divide-gray-100">
              {data.tasks.map((task) => (
                <li key={task.id} className="py-3 flex items-center justify-between gap-3 group">
                  <div className="flex items-center gap-3 min-w-0">
                    <input
                      type="checkbox"
                      checked={task.status === "completed"}
                      onChange={() => handleToggleTask(task)}
                      className="w-4 h-4 text-indigo-600 rounded"
                    />
                    <div className="min-w-0">
                      <div
                        className={`text-sm font-medium truncate ${
                          task.status === "completed" ? "line-through text-gray-400" : "text-gray-900"
                        }`}
                      >
                        {task.title}
                      </div>
                      {task.due_at && (
                        <div className="text-xs text-gray-500">
                          Due {new Date(task.due_at).toLocaleDateString()}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className={`text-xs ${priorityColors[task.priority] || ""}`}>
                      {task.priority}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${statusColors[task.status] || ""}`}>
                      {task.status.replace("_", " ")}
                    </span>
                    <button
                      onClick={() => handleDeleteTask(task.id)}
                      className="text-xs text-gray-400 hover:text-rose-600 opacity-0 group-hover:opacity-100 transition-opacity"
                      title="Delete task"
                    >
                      ✕
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        {/* Notifications */}
        <Panel
          title="Notifications"
          icon="🔔"
          subtitle={counts.unread_notifications > 0 ? `${counts.unread_notifications} unread` : "All caught up"}
          defaultCollapsed={false}
          action={
            counts.unread_notifications > 0 ? (
              <button
                onClick={handleMarkAllRead}
                className="text-xs text-indigo-600 hover:text-indigo-800 px-2 py-1 rounded-md hover:bg-indigo-50 transition-colors"
              >
                Mark all read
              </button>
            ) : undefined
          }
        >
          {!data?.notifications?.length ? (
            <p className="text-sm text-gray-500 py-8 text-center">No notifications.</p>
          ) : (
            <ul className="space-y-2 max-h-72 overflow-y-auto pr-1" style={{ scrollbarWidth: "thin" }}>
              {data.notifications.map((n) => (
                <li
                  key={n.id}
                  className={`p-3 rounded-xl border text-sm transition-colors ${
                    n.read ? "border-gray-100 bg-white" : "border-indigo-200 bg-indigo-50"
                  }`}
                >
                  <div className="font-medium text-gray-900">{n.subject}</div>
                  <div className="text-xs text-gray-500 mt-0.5">
                    {n.notification_type.replace(/_/g, " ")}
                    {n.created_at && ` · ${new Date(n.created_at).toLocaleString()}`}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {/* ── Recent Activity ──────────────────────────────── */}
      <Panel
        title="Recent Activity"
        icon="🕐"
        subtitle="Your last actions in ContractOS"
        defaultCollapsed={false}
      >
        {!data?.recent_activity?.length ? (
          <p className="text-sm text-gray-500 py-6 text-center">
            No activity yet. Your actions will appear here.
          </p>
        ) : (
          <ul className="space-y-3">
            {data.recent_activity.map((a) => (
              <li key={a.id} className="flex items-start gap-3 group">
                <span className="mt-1.5 w-2 h-2 rounded-full bg-indigo-500 shrink-0 group-hover:scale-125 transition-transform" />
                <div>
                  <div className="text-sm text-gray-800">
                    {a.summary || a.action.replace(/_/g, " ")}
                  </div>
                  <div className="text-xs text-gray-500">
                    {a.action.replace(/_/g, " ")}
                    {a.created_at && ` · ${new Date(a.created_at).toLocaleString()}`}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* ── Quick Actions ────────────────────────────────── */}
      <Panel
        title="Quick Actions"
        icon="⚡"
        subtitle="Common shortcuts"
        defaultCollapsed={false}
      >
        <div className="flex flex-wrap gap-3">
          <Link
            href="/agreements/new"
            className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors"
          >
            + New Agreement
          </Link>
          <Link
            href="/agreements/analyze"
            className="px-4 py-2 bg-purple-50 text-purple-700 border border-purple-200 text-sm font-medium rounded-lg hover:bg-purple-100 transition-colors"
          >
            🤖 AI Analyze
          </Link>
          <Link
            href="/execution"
            className="px-4 py-2 bg-orange-50 text-orange-700 border border-orange-200 text-sm font-medium rounded-lg hover:bg-orange-100 transition-colors"
          >
            ✍️ Sign / Execute
          </Link>
          <Link
            href="/bulk"
            className="px-4 py-2 bg-blue-50 text-blue-700 border border-blue-200 text-sm font-medium rounded-lg hover:bg-blue-100 transition-colors"
          >
            📦 Bulk Import
          </Link>
          <Link
            href="/audit"
            className="px-4 py-2 bg-emerald-50 text-emerald-700 border border-emerald-200 text-sm font-medium rounded-lg hover:bg-emerald-100 transition-colors"
          >
            🔗 Audit Trail
          </Link>
          <Link
            href="/risk"
            className="px-4 py-2 bg-rose-50 text-rose-700 border border-rose-200 text-sm font-medium rounded-lg hover:bg-rose-100 transition-colors"
          >
            ⚠️ Risk Graph
          </Link>
        </div>
      </Panel>
    </div>
  );
}

/* ── Helpers ────────────────────────────────────────────────── */
function timeOfDay() {
  const h = new Date().getHours();
  if (h < 12) return "morning";
  if (h < 17) return "afternoon";
  return "evening";
}
