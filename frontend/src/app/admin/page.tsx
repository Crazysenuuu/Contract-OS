"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  getAdminHealth,
  getAdminOverview,
  getAdminActiveSessions,
  getAdminSessionHistory,
  getAdminCompanies,
  getAdminUsers,
  adminPromoteUser,
  adminDemoteUser,
  adminActivateUser,
  adminDeactivateUser,
  AdminSession,
  AdminUser,
} from "@/lib/api";

interface Overview {
  generated_at: string;
  users: { total: number; active: number; admins: number };
  companies: number;
  agreements: { total: number; by_status: Record<string, number> };
  sessions: { active_now: number; active_users_now: number; last_24h: number };
}

interface Health {
  status: string;
  timestamp: string;
  services: Record<
    string,
    {
      status: string;
      message?: string;
      consecutive_failures?: number;
      recent_failures_24h?: number;
      active_sessions?: number;
    }
  >;
}

interface Company {
  organization_id: string;
  name: string;
  slug: string;
  country: string;
  users: number;
  active_users: number;
}

export default function AdminPage() {
  const { user, token, isLoading } = useAuth();
  const router = useRouter();

  const [overview, setOverview] = useState<Overview | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [active, setActive] = useState<AdminSession[]>([]);
  const [history, setHistory] = useState<AdminSession[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"active" | "history" | "companies" | "users">("active");

  useEffect(() => {
    if (!isLoading && !token) {
      router.replace("/login");
      return;
    }
    if (!isLoading && token && user && !user.is_admin) {
      router.replace("/dashboard");
      return;
    }
  }, [isLoading, token, user, router]);

  // No leading setError(null): avoids a synchronous setState cascade from
  // the mount effect; errors are cleared inside the async callbacks.
  const reload = useCallback(() => {
    if (!token) return;
    Promise.all([
      getAdminOverview(token),
      getAdminHealth(token),
      getAdminActiveSessions(token),
      getAdminSessionHistory(token, 50),
      getAdminCompanies(token),
      getAdminUsers(token),
    ])
      .then(([o, h, a, hist, c, u]) => {
        setOverview(o);
        setHealth(h);
        setActive(a);
        setHistory(hist);
        setCompanies(c);
        setUsers(u);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load admin data"))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (token && user?.is_admin) reload();
  }, [token, user, reload]);

  if (isLoading || loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-brand-600 bg-transparent">
        <div className="flex flex-col items-center animate-pulse">
          <div className="w-12 h-12 border-4 border-brand-200 border-t-brand-600 rounded-full animate-spin mb-4"></div>
          <span className="font-semibold tracking-wide uppercase text-sm">Initializing System</span>
        </div>
      </div>
    );
  }

  if (!user?.is_admin) {
    return null;
  }

  return (
    <div className="min-h-screen font-sans text-gray-900 bg-transparent animate-fade-in">
      {/* Decorative background orbs */}
      <div className="orb w-96 h-96 -top-20 -left-20 bg-gradient-to-br from-brand-200/60 to-purple-200/50 animate-float"></div>
      <div className="orb w-80 h-80 top-1/3 -right-24 bg-gradient-to-bl from-indigo-200/60 to-brand-100/40 animate-float" style={{ animationDelay: '2s' }}></div>

      {/* Floating Glass Header */}
      <header className="fixed top-0 left-0 right-0 z-50 px-4 py-4">
        <div className="max-w-7xl mx-auto glass-panel rounded-full px-6 py-3 flex items-center justify-between">
          <div className="flex items-center space-x-6">
            <Link href="/dashboard" className="text-sm font-semibold text-gray-500 hover:text-brand-600 transition-colors flex items-center">
              <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path></svg>
              Exit Admin
            </Link>
            <div className="h-4 w-px bg-gray-300"></div>
            <span className="text-lg font-bold bg-clip-text text-transparent bg-gradient-to-r from-purple-600 to-indigo-500">
              Admin Console
            </span>
          </div>
          <span className="px-3 py-1 text-[11px] uppercase tracking-widest font-bold rounded-full bg-gradient-to-r from-purple-500 to-indigo-500 text-white shadow-md">
            Superuser
          </span>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-32 pb-12">
        {error && (
          <div className="mb-8 glass-card border-rose-200 bg-rose-50/80 px-4 py-4 rounded-xl text-sm flex items-center animate-fade-in-up">
            <svg className="w-5 h-5 text-rose-500 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            <span className="text-rose-800 font-medium">{error}</span>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-10">
          <StatCard delay="0.1s" label="Total Users" value={overview?.users.total ?? 0} sub={`${overview?.users.active ?? 0} active currently`} icon="users" />
          <StatCard delay="0.2s" label="Companies" value={overview?.companies ?? 0} icon="office-building" />
          <StatCard delay="0.3s" label="Agreements" value={overview?.agreements.total ?? 0} sub={`${overview?.sessions.last_24h ?? 0} sessions / 24h`} icon="document-text" />
          <StatCard delay="0.4s" label="Online Now" value={overview?.sessions.active_users_now ?? 0} sub={`${overview?.sessions.active_now ?? 0} active sessions`} icon="status-online" />
        </div>

        {/* Admin tools */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-10 animate-fade-in-up" style={{ animationDelay: '0.45s' }}>
          <Link href="/admin/ingestion" className="glass-card rounded-2xl px-5 py-4 flex items-center justify-between hover:border-indigo-300 transition-colors group">
            <div>
              <p className="text-sm font-bold text-gray-900">Contract Ingestion</p>
              <p className="text-xs text-gray-500">Bulk OCR upload + human review queue</p>
            </div>
            <span className="text-indigo-500 group-hover:translate-x-1 transition-transform">→</span>
          </Link>
          <Link href="/admin/approval-rules" className="glass-card rounded-2xl px-5 py-4 flex items-center justify-between hover:border-indigo-300 transition-colors group">
            <div>
              <p className="text-sm font-bold text-gray-900">Approval Routing Rules</p>
              <p className="text-xs text-gray-500">Visual builder for DOA approval automation</p>
            </div>
            <span className="text-indigo-500 group-hover:translate-x-1 transition-transform">→</span>
          </Link>
          <Link href="/admin/feature-flags" className="glass-card rounded-2xl px-5 py-4 flex items-center justify-between hover:border-indigo-300 transition-colors group">
            <div>
              <p className="text-sm font-bold text-gray-900">Feature Flags</p>
              <p className="text-xs text-gray-500">Toggle and roll out platform features</p>
            </div>
            <span className="text-indigo-500 group-hover:translate-x-1 transition-transform">→</span>
          </Link>
          <Link href="/admin/audit-batches" className="glass-card rounded-2xl px-5 py-4 flex items-center justify-between hover:border-indigo-300 transition-colors group">
            <div>
              <p className="text-sm font-bold text-gray-900">Audit Batch Sealing</p>
              <p className="text-xs text-gray-500">Merkle-seal audit chains + verify TSA anchors</p>
            </div>
            <span className="text-indigo-500 group-hover:translate-x-1 transition-transform">→</span>
          </Link>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 mb-8">
          <div className="lg:col-span-2 glass-card rounded-2xl overflow-hidden animate-fade-in-up" style={{ animationDelay: '0.5s' }}>
            <div className="px-6 py-5 border-b border-gray-100/50 bg-white/30 flex flex-col sm:flex-row sm:items-center justify-between">
              <h2 className="text-xl font-bold text-gray-900 mb-4 sm:mb-0">System Activity</h2>
              <div className="flex flex-wrap items-center gap-1 bg-gray-100/50 p-1 rounded-xl">
                <TabButton active={tab === "active"} onClick={() => setTab("active")}>
                  Live Sessions
                </TabButton>
                <TabButton active={tab === "history"} onClick={() => setTab("history")}>
                  History
                </TabButton>
                <TabButton active={tab === "companies"} onClick={() => setTab("companies")}>
                  Organizations
                </TabButton>
                <TabButton active={tab === "users"} onClick={() => setTab("users")}>
                  Users
                </TabButton>
              </div>
            </div>

            {tab === "active" && <ActiveSessionsTable rows={active} />}
            {tab === "history" && <SessionHistoryTable rows={history} />}
            {tab === "companies" && <CompaniesTable rows={companies} />}
            {tab === "users" && (
              <UsersTable
                rows={users}
                currentUserId={user?.id}
                token={token ?? ""}
                onMutated={() => reload()}
              />
            )}
          </div>

          <div className="glass-card rounded-2xl p-6 h-fit animate-fade-in-up" style={{ animationDelay: '0.6s' }}>
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-gray-900">System Health</h2>
              {health && <StatusBadge status={health.status} />}
            </div>
            {health ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between p-3 rounded-xl bg-white/40 border border-white/50 hover:bg-white/60 transition-colors">
                  <div className="flex items-center">
                    <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center mr-3">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    </div>
                    <span className="text-sm font-semibold text-gray-700">Core API</span>
                  </div>
                  <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse-soft"></div>
                </div>
                {Object.entries(health.services).map(([name, svc]) => (
                  <div key={name} className="flex items-center justify-between p-3 rounded-xl bg-white/40 border border-white/50 hover:bg-white/60 transition-colors">
                    <div className="flex items-center">
                      <div className="w-8 h-8 rounded-full bg-gray-100 text-gray-500 flex items-center justify-center mr-3">
                        <span className="text-xs font-bold uppercase">{name.substring(0,2)}</span>
                      </div>
                      <span className="text-sm font-semibold text-gray-700 capitalize">{name.replace("_", " ")}</span>
                    </div>
                    <StatusBadge status={svc.status} detail={svc.active_sessions != null ? `${svc.active_sessions}` : undefined} />
                  </div>
                ))}
                <div className="pt-4 mt-2 border-t border-gray-100/50">
                  <p className="text-xs text-gray-400 text-center">
                    Last checked: {new Date(health.timestamp).toLocaleTimeString()}
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-10">
                <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center mb-3">
                  <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                </div>
                <p className="text-gray-500 text-sm font-medium">Telemetry unavailable</p>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function StatCard({ label, value, sub, delay, icon }: { label: string; value: number; sub?: string; delay: string; icon: string }) {
  return (
    <div className="glass-card rounded-2xl p-6 animate-fade-in-up hover:-translate-y-1 hover:shadow-xl hover:border-brand-200/60" style={{ animationDelay: delay }}>
      <div className="flex items-center justify-between mb-4">
        <div className="text-sm font-semibold text-gray-500 uppercase tracking-wider">{label}</div>
        <div className="w-10 h-10 rounded-full bg-brand-50 flex items-center justify-center text-brand-600">
          {icon === 'users' && <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>}
          {icon === 'office-building' && <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"></path></svg>}
          {icon === 'document-text' && <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>}
          {icon === 'status-online' && <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5.121 17.804A13.937 13.937 0 0112 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 11-6 0 3 3 0 016 0zm6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>}
        </div>
      </div>
      <div className="text-4xl font-extrabold text-gray-900 tracking-tight">{value}</div>
      {sub && <div className="mt-2 text-xs font-medium text-gray-500">{sub}</div>}
    </div>
  );
}

function StatusBadge({ status, detail }: { status: string; detail?: string }) {
  const color =
    status === "healthy"
      ? "bg-emerald-100 text-emerald-800 border-emerald-200"
      : status === "degraded"
        ? "bg-amber-100 text-amber-800 border-amber-200"
        : "bg-rose-100 text-rose-800 border-rose-200";
  return (
    <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border shadow-sm ${color}`}>
      {status}
      {detail ? ` (${detail})` : ""}
    </span>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-xs font-bold rounded-lg transition-all duration-200 ${
        active
          ? "bg-gradient-to-r from-brand-600 to-purple-500 text-white shadow-md shadow-brand-500/20 scale-[1.02]"
          : "text-gray-500 hover:text-gray-800 hover:bg-white/50"
      }`}
    >
      {children}
    </button>
  );
}

function ActiveSessionsTable({ rows }: { rows: AdminSession[] }) {
  if (rows.length === 0) {
    return (
      <div className="px-6 py-16 flex flex-col items-center justify-center">
        <div className="w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center mb-4">
          <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>
        </div>
        <p className="text-gray-500 font-medium">No users online right now</p>
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-100/50">
        <thead className="bg-gray-50/30">
          <tr>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">User</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Company</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Logged In</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Last Seen</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Online</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">IP</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100/50 bg-white/10">
          {rows.map((s) => (
            <tr key={s.session_id} className="row-hover">
              <td className="px-6 py-4">
                <div className="flex items-center">
                  <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-brand-400 to-brand-600 text-white flex items-center justify-center font-bold text-xs mr-3 shadow-sm">
                    {s.name.charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <div className="text-sm font-bold text-gray-900">{s.name}</div>
                    <div className="text-xs text-gray-500">{s.email}</div>
                  </div>
                </div>
              </td>
              <td className="px-6 py-4 text-sm font-medium text-gray-700">{s.company ?? "—"}</td>
              <td className="px-6 py-4 text-sm text-gray-600">{new Date(s.login_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</td>
              <td className="px-6 py-4 text-sm text-gray-500">{s.last_seen_at ? new Date(s.last_seen_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : "—"}</td>
              <td className="px-6 py-4">
                <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-100 flex items-center w-max">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5 animate-pulse-soft"></span>
                  {s.online_duration}
                </span>
              </td>
              <td className="px-6 py-4 text-xs font-mono text-gray-400">{s.ip_address ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SessionHistoryTable({ rows }: { rows: AdminSession[] }) {
  if (rows.length === 0) {
    return <div className="px-6 py-16 text-center text-gray-500 font-medium">No session history available</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-100/50">
        <thead className="bg-gray-50/30">
          <tr>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">User</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Company</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Logged In</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Logged Out</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Duration</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100/50 bg-white/10">
          {rows.map((s) => (
            <tr key={s.session_id} className="row-hover">
              <td className="px-6 py-4">
                <div className="text-sm font-bold text-gray-900">{s.name}</div>
                <div className="text-xs text-gray-500">{s.email}</div>
              </td>
              <td className="px-6 py-4 text-sm font-medium text-gray-700">{s.company ?? "—"}</td>
              <td className="px-6 py-4 text-sm text-gray-600">{new Date(s.login_at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</td>
              <td className="px-6 py-4 text-sm text-gray-500">{s.logout_at ? new Date(s.logout_at).toLocaleString([], {hour:'2-digit', minute:'2-digit'}) : "—"}</td>
              <td className="px-6 py-4 text-sm font-medium text-gray-700">{s.online_duration}</td>
              <td className="px-6 py-4">
                <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border shadow-sm ${
                  s.status === "active" ? "bg-emerald-100 text-emerald-800 border-emerald-200" : "bg-gray-100 text-gray-600 border-gray-200"
                }`}>
                  {s.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CompaniesTable({ rows }: { rows: Company[] }) {
  if (rows.length === 0) {
    return <div className="px-6 py-16 text-center text-gray-500 font-medium">No companies registered yet</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-100/50">
        <thead className="bg-gray-50/30">
          <tr>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Company</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Country</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Members</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Active Now</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100/50 bg-white/10">
          {rows.map((c) => (
            <tr key={c.organization_id} className="row-hover">
              <td className="px-6 py-4">
                <div className="flex items-center">
                  <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-gray-100 to-gray-200 border border-gray-300 shadow-inner flex items-center justify-center mr-4">
                    <span className="font-extrabold text-gray-500 text-lg">{c.name.charAt(0)}</span>
                  </div>
                  <div>
                    <div className="text-sm font-bold text-gray-900">{c.name}</div>
                    <div className="text-xs text-gray-500 uppercase tracking-wider">{c.slug}</div>
                  </div>
                </div>
              </td>
              <td className="px-6 py-4 text-sm font-medium text-gray-700 flex items-center">
                <span className="w-5 h-5 rounded-full bg-gray-100 flex items-center justify-center text-xs mr-2 border border-gray-200">
                  📍
                </span>
                {c.country ?? "—"}
              </td>
              <td className="px-6 py-4 text-sm font-bold text-gray-900">{c.users}</td>
              <td className="px-6 py-4">
                {c.active_users > 0 ? (
                  <span className="px-3 py-1 text-xs font-bold rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-100 inline-flex items-center">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5 animate-pulse-soft"></span>
                    {c.active_users}
                  </span>
                ) : (
                  <span className="text-sm font-medium text-gray-400">0</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UsersTable({
  rows,
  currentUserId,
  token,
  onMutated,
}: {
  rows: AdminUser[];
  currentUserId?: string;
  token: string;
  onMutated: () => void;
}) {
  const [actionError, setActionError] = useState<string | null>(null);

  if (rows.length === 0) {
    return <div className="px-6 py-16 text-center text-gray-500 font-medium">No users registered yet</div>;
  }

  const run = async (fn: (t: string, id: string) => Promise<unknown>, user: AdminUser) => {
    setActionError(null);
    try {
      await fn(token, user.user_id);
      onMutated();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Action failed");
    }
  };

  return (
    <div className="overflow-x-auto">
      {actionError && (
        <div className="px-6 py-3 text-sm text-rose-700 bg-rose-50 border-b border-rose-100">
          {actionError}
        </div>
      )}
      <table className="min-w-full divide-y divide-gray-100/50">
        <thead className="bg-gray-50/30">
          <tr>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">User</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Company</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Role</th>
            <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Signed Up</th>
            <th className="px-6 py-4 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100/50 bg-white/10">
          {rows.map((u) => (
            <tr key={u.user_id} className="row-hover">
              <td className="px-6 py-4">
                <div className="flex items-center">
                  <div className="w-9 h-9 rounded-full bg-gradient-to-tr from-brand-400 to-indigo-500 text-white flex items-center justify-center font-bold text-sm mr-3 shadow-sm">
                    {u.name.charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <div className="text-sm font-bold text-gray-900">
                      {u.name}
                      {u.user_id === currentUserId && (
                        <span className="ml-2 text-[10px] uppercase tracking-wider font-bold text-brand-600">(you)</span>
                      )}
                    </div>
                    <div className="text-xs text-gray-500">{u.email}</div>
                  </div>
                </div>
              </td>
              <td className="px-6 py-4 text-sm font-medium text-gray-700">{u.organization ?? "—"}</td>
              <td className="px-6 py-4">
                <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border shadow-sm ${
                  u.status === "active"
                    ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                    : u.status === "pending_verification"
                      ? "bg-amber-100 text-amber-800 border-amber-200"
                      : "bg-rose-100 text-rose-800 border-rose-200"
                }`}>
                  {u.status.replace("_", " ")}
                </span>
              </td>
              <td className="px-6 py-4">
                {u.is_admin ? (
                  <span className="px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full bg-purple-100 text-purple-800 border border-purple-200 shadow-sm">
                    Admin
                  </span>
                ) : (
                  <span className="px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full bg-gray-100 text-gray-600 border border-gray-200 shadow-sm">
                    User
                  </span>
                )}
              </td>
              <td className="px-6 py-4 text-xs text-gray-500">
                {u.created_at ? new Date(u.created_at).toLocaleDateString([], {month: 'short', day: 'numeric', year: 'numeric'}) : "—"}
              </td>
              <td className="px-6 py-4">
                <div className="flex flex-wrap items-center justify-end gap-2">
                  {u.user_id !== currentUserId && !u.is_admin && (
                    <button
                      onClick={() => run(adminPromoteUser, u)}
                      className="px-3 py-1.5 text-xs font-bold rounded-lg bg-gradient-to-r from-brand-600 to-purple-500 text-white shadow-sm hover:opacity-90 transition-opacity"
                    >
                      Promote
                    </button>
                  )}
                  {u.user_id !== currentUserId && u.is_admin && (
                    <button
                      onClick={() => run(adminDemoteUser, u)}
                      className="px-3 py-1.5 text-xs font-bold rounded-lg bg-amber-100 text-amber-800 hover:bg-amber-200 transition-colors"
                    >
                      Demote
                    </button>
                  )}
                  {u.user_id !== currentUserId && u.status === "deactivated" && (
                    <button
                      onClick={() => run(adminActivateUser, u)}
                      className="px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-colors"
                    >
                      Reactivate
                    </button>
                  )}
                  {u.user_id !== currentUserId && u.status !== "deactivated" && (
                    <button
                      onClick={() => run(adminDeactivateUser, u)}
                      className="px-3 py-1.5 text-xs font-bold rounded-lg bg-rose-50 text-rose-700 hover:bg-rose-100 transition-colors"
                    >
                      Deactivate
                    </button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}