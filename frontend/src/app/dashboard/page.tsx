"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  getDashboard,
  createMyTask,
  updateMyTask,
  deleteMyTask,
  markAllNotificationsRead,
  DashboardPayload,
  DashboardTask,
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
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  const counts = data?.counts || {
    pending_tasks: 0,
    unread_notifications: 0,
    agreements: 0,
    executed_agreements: 0,
    open_obligations: 0,
  };

  const statCards = [
    { label: "Agreements", value: counts.agreements, href: "/dashboard", color: "from-blue-500 to-indigo-500" },
    { label: "Executed", value: counts.executed_agreements, href: "/search?status=executed", color: "from-emerald-500 to-teal-500" },
    { label: "My Tasks", value: counts.pending_tasks, href: "/dashboard", color: "from-amber-500 to-orange-500" },
    { label: "Unread Alerts", value: counts.unread_notifications, href: "/outbox", color: "from-rose-500 to-pink-500" },
    { label: "Open Obligations", value: counts.open_obligations, href: "/agreements/obligations", color: "from-purple-500 to-violet-500" },
  ];

  return (
    <div className="space-y-6">
      {/* Header + Search */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Welcome back, {user?.name?.split(" ")[0] || "there"} 👋
          </h1>
          <p className="text-sm text-gray-500 mt-1">
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
            className="px-3 py-2 border border-gray-300 rounded-md text-sm w-56 focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
          <button
            type="submit"
            className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
          >
            Search
          </button>
        </form>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {statCards.map((card) => (
          <Link
            key={card.label}
            href={card.href}
            className="bg-white rounded-xl shadow-sm border border-gray-100 p-5 hover:shadow-md transition-shadow"
          >
            <div
              className={`w-9 h-9 rounded-lg bg-gradient-to-br ${card.color} flex items-center justify-center text-white text-sm font-bold mb-3`}
            >
              {card.value}
            </div>
            <div className="text-sm font-medium text-gray-700">{card.label}</div>
          </Link>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* My Tasks */}
        <div className="lg:col-span-2 bg-white rounded-xl shadow-sm border border-gray-100 p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900">My Tasks</h2>
            <button
              onClick={() => setShowTaskForm(!showTaskForm)}
              className="text-sm text-brand-600 hover:text-brand-800 font-medium"
            >
              {showTaskForm ? "Cancel" : "+ New Task"}
            </button>
          </div>

          {showTaskForm && (
            <div className="mb-4 p-4 bg-gray-50 rounded-lg space-y-3">
              <input
                type="text"
                value={taskTitle}
                onChange={(e) => setTaskTitle(e.target.value)}
                placeholder="Task title"
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              />
              <div className="flex gap-3">
                <input
                  type="date"
                  value={taskDue}
                  onChange={(e) => setTaskDue(e.target.value)}
                  className="border border-gray-300 rounded-md px-3 py-2 text-sm"
                />
                <select
                  value={taskPriority}
                  onChange={(e) => setTaskPriority(e.target.value)}
                  className="border border-gray-300 rounded-md px-3 py-2 text-sm"
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
                <button
                  onClick={handleAddTask}
                  disabled={addingTask || !taskTitle.trim()}
                  className="px-4 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-50"
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
                <li key={task.id} className="py-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <input
                      type="checkbox"
                      checked={task.status === "completed"}
                      onChange={() => handleToggleTask(task)}
                      className="w-4 h-4 text-brand-600 rounded"
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
                      className="text-xs text-gray-400 hover:text-rose-600"
                      title="Delete task"
                    >
                      ✕
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Notifications */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900">Notifications</h2>
            {counts.unread_notifications > 0 && (
              <button
                onClick={handleMarkAllRead}
                className="text-xs text-brand-600 hover:text-brand-800"
              >
                Mark all read
              </button>
            )}
          </div>
          {!data?.notifications?.length ? (
            <p className="text-sm text-gray-500 py-8 text-center">No notifications.</p>
          ) : (
            <ul className="space-y-3 max-h-80 overflow-y-auto">
              {data.notifications.map((n) => (
                <li
                  key={n.id}
                  className={`p-3 rounded-lg border text-sm ${
                    n.read ? "border-gray-100 bg-white" : "border-brand-200 bg-brand-50"
                  }`}
                >
                  <div className="font-medium text-gray-900">{n.subject}</div>
                  <div className="text-xs text-gray-500 mt-1">
                    {n.notification_type.replace(/_/g, " ")}
                    {n.created_at &&
                      ` · ${new Date(n.created_at).toLocaleString()}`}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Recent Activity */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Recent Activity</h2>
        {!data?.recent_activity?.length ? (
          <p className="text-sm text-gray-500 py-6 text-center">
            No activity yet. Your actions will appear here.
          </p>
        ) : (
          <ul className="space-y-3">
            {data.recent_activity.map((a) => (
              <li key={a.id} className="flex items-start gap-3">
                <span className="mt-1 w-2 h-2 rounded-full bg-brand-500 shrink-0"></span>
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
      </div>

      {/* Quick Actions */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Quick Actions</h2>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/agreements/new"
            className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
          >
            + New Agreement
          </Link>
          <Link
            href="/agreements/analyze"
            className="px-4 py-2 bg-purple-50 text-purple-700 border border-purple-200 text-sm font-medium rounded-md hover:bg-purple-100"
          >
            AI Analyze
          </Link>
          <Link
            href="/execution"
            className="px-4 py-2 bg-orange-50 text-orange-700 border border-orange-200 text-sm font-medium rounded-md hover:bg-orange-100"
          >
            ✍️ Sign / Execute
          </Link>
          <Link
            href="/bulk"
            className="px-4 py-2 bg-blue-50 text-blue-700 border border-blue-200 text-sm font-medium rounded-md hover:bg-blue-100"
          >
            Bulk Import
          </Link>
          <Link
            href="/audit"
            className="px-4 py-2 bg-emerald-50 text-emerald-700 border border-emerald-200 text-sm font-medium rounded-md hover:bg-emerald-100"
          >
            🔗 Audit Trail
          </Link>
        </div>
      </div>
    </div>
  );
}