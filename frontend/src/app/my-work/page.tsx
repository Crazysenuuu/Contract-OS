"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listMyTasks,
  createMyTask,
  updateMyTask,
  deleteMyTask,
  DashboardTask,
} from "@/lib/api";

const priorityStyles: Record<string, string> = {
  urgent: "bg-rose-100 text-rose-700",
  high: "bg-orange-100 text-orange-700",
  normal: "bg-gray-100 text-gray-600",
  low: "bg-gray-50 text-gray-400",
};

const statusStyles: Record<string, string> = {
  pending: "bg-amber-50 text-amber-700 border-amber-200",
  in_progress: "bg-blue-50 text-blue-700 border-blue-200",
  completed: "bg-emerald-50 text-emerald-700 border-emerald-200",
  cancelled: "bg-gray-100 text-gray-500 border-gray-200",
};

function dueLabel(dueAt: string | null): { text: string; overdue: boolean } {
  if (!dueAt) return { text: "No due date", overdue: false };
  const d = new Date(dueAt);
  const overdue = d.getTime() < Date.now();
  return {
    text: d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }),
    overdue,
  };
}

type StatusTab = "pending" | "in_progress" | "completed";

export default function MyWorkPage() {
  const { token } = useAuth();
  const [tasks, setTasks] = useState<DashboardTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<StatusTab>("pending");

  // Create form
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState("normal");
  const [dueAt, setDueAt] = useState("");
  const [saving, setSaving] = useState(false);

  const reload = useCallback(() => {
    if (!token) return;
    listMyTasks(token)
      .then(setTasks)
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Failed to load tasks")
      )
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  const visible = tasks.filter((t) => {
    if (tab === "completed") return t.status === "completed";
    if (tab === "in_progress") return t.status === "in_progress";
    return t.status === "pending";
  });

  const counts = {
    pending: tasks.filter((t) => t.status === "pending").length,
    in_progress: tasks.filter((t) => t.status === "in_progress").length,
    completed: tasks.filter((t) => t.status === "completed").length,
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !title.trim()) return;
    setSaving(true);
    setError("");
    try {
      await createMyTask(token, {
        title: title.trim(),
        priority,
        due_at: dueAt ? new Date(dueAt).toISOString() : undefined,
      });
      setTitle("");
      setDueAt("");
      setPriority("normal");
      setShowForm(false);
      setTab("pending");
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setSaving(false);
    }
  };

  const handleStatus = async (task: DashboardTask, status: string) => {
    if (!token) return;
    setError("");
    try {
      await updateMyTask(token, task.id, { status });
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update task");
    }
  };

  const handleDelete = async (task: DashboardTask) => {
    if (!token) return;
    setError("");
    try {
      await deleteMyTask(token, task.id);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete task");
    }
  };

  const tabButton = (key: StatusTab, label: string) => (
    <button
      key={key}
      onClick={() => setTab(key)}
      className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
        tab === key
          ? "bg-gray-900 text-white"
          : "text-gray-600 hover:bg-gray-100"
      }`}
    >
      {label} ({counts[key]})
    </button>
  );

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">My Work</h1>
          <p className="mt-1 text-sm text-gray-500">
            Your personal task inbox — everything assigned to you.
          </p>
        </div>
        <button
          onClick={() => setShowForm((s) => !s)}
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700"
        >
          {showForm ? "Cancel" : "+ New Task"}
        </button>
      </div>

      {error && (
        <div className="mb-6 bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded text-sm">
          {error}
        </div>
      )}

      {/* Create form */}
      {showForm && (
        <form
          onSubmit={handleCreate}
          className="mb-6 bg-white shadow rounded-lg p-4 space-y-3"
        >
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What needs to be done?"
            required
            className="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
          />
          <div className="flex flex-wrap gap-3">
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-md text-sm"
            >
              <option value="low">Low priority</option>
              <option value="normal">Normal priority</option>
              <option value="high">High priority</option>
              <option value="urgent">Urgent</option>
            </select>
            <input
              type="datetime-local"
              value={dueAt}
              onChange={(e) => setDueAt(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-md text-sm"
            />
            <button
              type="submit"
              disabled={saving || !title.trim()}
              className="ml-auto px-4 py-2 text-sm font-medium text-white bg-emerald-600 rounded-md hover:bg-emerald-700 disabled:opacity-50"
            >
              {saving ? "Adding…" : "Add Task"}
            </button>
          </div>
        </form>
      )}

      {/* Status tabs */}
      <div className="flex flex-wrap gap-2 mb-4">
        {tabButton("pending", "Pending")}
        {tabButton("in_progress", "In Progress")}
        {tabButton("completed", "Completed")}
      </div>

      {/* Task list */}
      <div className="bg-white shadow rounded-lg divide-y divide-gray-100 overflow-hidden">
        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading tasks…</div>
        ) : visible.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            {tab === "pending"
              ? "Nothing pending — you're all caught up."
              : tab === "in_progress"
              ? "No tasks in progress."
              : "No completed tasks yet."}
          </div>
        ) : (
          visible.map((t) => {
            const due = dueLabel(t.due_at);
            return (
              <div key={t.id} className="p-4 flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                        priorityStyles[t.priority] ?? priorityStyles.normal
                      }`}
                    >
                      {t.priority}
                    </span>
                    <span className="text-sm font-medium text-gray-900">
                      {t.title}
                    </span>
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                        statusStyles[t.status] ?? statusStyles.pending
                      }`}
                    >
                      {t.status.replace(/_/g, " ")}
                    </span>
                  </div>
                  {t.description && (
                    <p className="mt-1 text-sm text-gray-600">{t.description}</p>
                  )}
                  <p className="mt-1 text-xs">
                    <span className={due.overdue ? "text-rose-600 font-medium" : "text-gray-400"}>
                      {t.status === "completed" ? "Was due" : "Due"} {due.text}
                    </span>
                    {t.agreement_id && (
                      <>
                        {" · "}
                        <Link
                          href={`/agreements/${t.agreement_id}`}
                          className="text-blue-600 hover:underline"
                        >
                          view agreement
                        </Link>
                      </>
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {t.status === "pending" && (
                    <button
                      onClick={() => handleStatus(t, "in_progress")}
                      className="px-2 py-1 text-[10px] font-medium rounded-md border border-gray-200 text-gray-600 hover:bg-gray-50"
                    >
                      start
                    </button>
                  )}
                  {t.status !== "completed" && (
                    <button
                      onClick={() => handleStatus(t, "completed")}
                      className="px-2 py-1 text-[10px] font-medium rounded-md border border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                    >
                      done
                    </button>
                  )}
                  {t.status === "pending" && (
                    <button
                      onClick={() => handleStatus(t, "cancelled")}
                      className="px-2 py-1 text-[10px] font-medium rounded-md border border-gray-200 text-gray-500 hover:bg-gray-50"
                    >
                      cancel
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(t)}
                    className="px-2 py-1 text-[10px] font-medium rounded-md border border-rose-200 text-rose-600 hover:bg-rose-50"
                    aria-label={`Delete task ${t.title}`}
                  >
                    ✕
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
