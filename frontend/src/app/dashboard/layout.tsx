"use client";

import { useAuth } from "@/contexts/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import NotificationBell from "@/components/NotificationBell";

/* ─── Nav config ─────────────────────────────────────────────── */
interface NavItem { label: string; href: string; icon: string }
interface NavGroup { heading: string; items: NavItem[] }

const NAV_GROUPS: NavGroup[] = [
  {
    heading: "Main",
    items: [
      { label: "Dashboard",      href: "/dashboard",    icon: "🏠" },
      { label: "Search",         href: "/search",       icon: "🔍" },
    ],
  },
  {
    heading: "Agreements",
    items: [
      { label: "All Agreements", href: "/agreements",      icon: "📄" },
      { label: "New Agreement",  href: "/agreements/new",  icon: "➕" },
      { label: "Templates",      href: "/templates",        icon: "📋" },
      { label: "Bulk Import",    href: "/bulk",             icon: "📦" },
    ],
  },
  {
    heading: "Workflow",
    items: [
      { label: "Approvals",   href: "/approvals",  icon: "✅" },
      { label: "E-Signature", href: "/esignature", icon: "✍️" },
      { label: "Execution",   href: "/execution",  icon: "🔏" },
      { label: "Renewals",    href: "/renewals",   icon: "🔄" },
    ],
  },
  {
    heading: "Monitoring",
    items: [
      { label: "Obligations", href: "/monitoring", icon: "📊" },
      { label: "Risk",        href: "/risk",        icon: "⚠️" },
      { label: "Analytics",   href: "/analytics",   icon: "📈" },
      { label: "Audit Trail", href: "/audit",       icon: "🔗" },
    ],
  },
  {
    heading: "Knowledge",
    items: [
      { label: "Clause Library",  href: "/clause-library",   icon: "📚" },
      { label: "Legal Knowledge", href: "/legal-knowledge",   icon: "⚖️" },
      { label: "Policies",        href: "/company-policies",  icon: "🏛️" },
      { label: "Intelligence",    href: "/intelligence",      icon: "🤖" },
    ],
  },
  {
    heading: "Contacts & Docs",
    items: [
      { label: "Contacts",     href: "/contacts",     icon: "👤" },
      { label: "Companies",    href: "/companies",    icon: "🏢" },
      { label: "Documents",    href: "/documents",    icon: "📁" },
      { label: "Jurisdictions",href: "/jurisdictions",icon: "🌍" },
    ],
  },
  {
    heading: "Platform",
    items: [
      { label: "Sign Auth",    href: "/signature-authority", icon: "🔐" },
      { label: "Translations", href: "/i18n",                icon: "🌐" },
      { label: "Billing",      href: "/billing",             icon: "💳" },
      { label: "Outbox",       href: "/outbox",              icon: "📡" },
      { label: "Observability",href: "/observability",       icon: "🔭" },
      { label: "Governance",   href: "/data-governance",     icon: "🛡️" },
    ],
  },
];

/* ─── Sidebar ─────────────────────────────────────────────────── */
function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [openGroups, setOpenGroups] = useState<Set<string>>(
    () => new Set(["Main", "Agreements", "Workflow"])
  );

  const toggle = (h: string) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(h)) {
        next.delete(h);
      } else {
        next.add(h);
      }
      return next;
    });

  return (
    <aside
      className={`
        flex flex-col h-screen sticky top-0
        bg-white border-r border-gray-200 shrink-0
        transition-[width] duration-300 ease-in-out overflow-hidden z-20
        ${collapsed ? "w-[56px]" : "w-[220px]"}
      `}
    >
      {/* Logo row */}
      <div className="flex items-center h-14 px-3 border-b border-gray-100 gap-2 shrink-0">
        {collapsed ? (
          <button
            onClick={onToggle}
            className="mx-auto text-xl leading-none"
            aria-label="Expand sidebar"
            title="Expand sidebar"
          >
            ⚖️
          </button>
        ) : (
          <>
            <Link href="/dashboard" className="flex items-center gap-2 flex-1 min-w-0">
              <span className="text-xl leading-none shrink-0">⚖️</span>
              <span className="text-sm font-bold text-gray-900 truncate">ContractOS</span>
            </Link>
            <button
              onClick={onToggle}
              className="shrink-0 text-gray-400 hover:text-gray-700 transition-colors"
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
            >
              <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none">
                <path d="M10 4L6 8l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-2" style={{ scrollbarWidth: "none" }}>
        {NAV_GROUPS.map((group) => {
          const open = openGroups.has(group.heading);
          return (
            <div key={group.heading} className="mb-1">
              {!collapsed && (
                <button
                  onClick={() => toggle(group.heading)}
                  className="w-full flex items-center justify-between px-3 py-1 group"
                >
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 group-hover:text-gray-600 transition-colors">
                    {group.heading}
                  </span>
                  <svg
                    className={`w-3 h-3 text-gray-400 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
                    viewBox="0 0 16 16" fill="none"
                  >
                    <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              )}

              {(collapsed || open) && (
                <div className={collapsed ? "px-1.5 space-y-0.5 py-0.5" : "px-2 space-y-0.5 pb-1"}>
                  {group.items.map((item) => {
                    const active = pathname === item.href || (item.href !== "/dashboard" && pathname?.startsWith(item.href + "/"));
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        title={collapsed ? item.label : undefined}
                        className={`
                          flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm font-medium
                          transition-all duration-150
                          ${active ? "bg-indigo-50 text-indigo-700" : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"}
                          ${collapsed ? "justify-center" : ""}
                        `}
                      >
                        <span className="text-base leading-none shrink-0">{item.icon}</span>
                        {!collapsed && <span className="truncate">{item.label}</span>}
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* Bottom user area */}
      <div className="border-t border-gray-100 p-2 shrink-0 space-y-0.5">
        <Link
          href="/settings"
          title={collapsed ? "Settings" : undefined}
          className={`flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm text-gray-600 hover:bg-gray-50 hover:text-gray-900 transition-colors ${collapsed ? "justify-center" : ""}`}
        >
          <span className="text-base leading-none">⚙️</span>
          {!collapsed && <span>Settings</span>}
        </Link>
        {!collapsed && user && (
          <div className="px-2 py-1 text-xs text-gray-500 truncate font-medium">{user.name}</div>
        )}
        <button
          onClick={logout}
          title={collapsed ? "Sign out" : undefined}
          className={`w-full flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm text-gray-600 hover:bg-rose-50 hover:text-rose-700 transition-colors ${collapsed ? "justify-center" : ""}`}
        >
          <span className="text-base leading-none">🚪</span>
          {!collapsed && <span>Sign out</span>}
        </button>
      </div>
    </aside>
  );
}

/* ─── Topbar ──────────────────────────────────────────────────── */
function Topbar() {
  const { user } = useAuth();
  return (
    <header className="h-14 bg-white border-b border-gray-200 flex items-center justify-between px-6 shrink-0 sticky top-0 z-10">
      <div className="text-sm text-gray-500">
        Welcome back, <span className="font-semibold text-gray-900">{user?.name?.split(" ")[0] ?? "there"}</span> 👋
      </div>
      <div className="flex items-center gap-3">
        <Link href="/search" aria-label="Search" className="text-gray-400 hover:text-gray-700 transition-colors">
          <svg className="w-5 h-5" viewBox="0 0 20 20" fill="none">
            <circle cx="8.5" cy="8.5" r="5.75" stroke="currentColor" strokeWidth="1.5"/>
            <path d="m13.5 13.5 3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </Link>
        <NotificationBell />
        <Link href="/settings" aria-label="Settings" className="text-gray-400 hover:text-gray-700 transition-colors">
          <svg className="w-5 h-5" viewBox="0 0 20 20" fill="none">
            <circle cx="10" cy="10" r="2.5" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M10 2v2m0 12v2M2 10h2m12 0h2m-3.05-4.95-1.41 1.41M6.46 13.54l-1.41 1.41M16.95 14.95l-1.41-1.41M6.46 6.46 5.05 5.05" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </Link>
      </div>
    </header>
  );
}

/* ─── Root layout ─────────────────────────────────────────────── */
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, isLoading } = useAuth();
  const router = useRouter();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
    }
  }, [user, isLoading, router]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 rounded-full border-2 border-indigo-600 border-t-transparent animate-spin" />
          <div className="text-sm text-gray-500">Loading…</div>
        </div>
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((c) => !c)}
      />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Topbar />
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
