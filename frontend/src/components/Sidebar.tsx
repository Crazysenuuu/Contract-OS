"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

interface NavItem {
  label: string;
  href: string;
  icon: string;
  badge?: string;
}

interface NavGroup {
  heading: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    heading: "Main",
    items: [
      { label: "Dashboard", href: "/dashboard", icon: "🏠" },
      { label: "Search", href: "/search", icon: "🔍" },
    ],
  },
  {
    heading: "Agreements",
    items: [
      { label: "All Agreements", href: "/agreements", icon: "📄" },
      { label: "New Agreement", href: "/agreements/new", icon: "➕" },
      { label: "Templates", href: "/templates", icon: "📋" },
      { label: "Bulk Import", href: "/bulk", icon: "📦" },
    ],
  },
  {
    heading: "Workflow",
    items: [
      { label: "Approvals", href: "/approvals", icon: "✅" },
      { label: "E-Signature", href: "/esignature", icon: "✍️" },
      { label: "Execution", href: "/execution", icon: "🔏" },
      { label: "Renewals", href: "/renewals", icon: "🔄" },
    ],
  },
  {
    heading: "Monitoring",
    items: [
      { label: "Obligations", href: "/monitoring", icon: "📊" },
      { label: "Risk", href: "/risk", icon: "⚠️" },
      { label: "Analytics", href: "/analytics", icon: "📈" },
      { label: "Audit Trail", href: "/audit", icon: "🔗" },
    ],
  },
  {
    heading: "Knowledge",
    items: [
      { label: "Clause Library", href: "/clause-library", icon: "📚" },
      { label: "Legal Knowledge", href: "/legal-knowledge", icon: "⚖️" },
      { label: "Company Policies", href: "/company-policies", icon: "🏛️" },
      { label: "Intelligence", href: "/intelligence", icon: "🤖" },
    ],
  },
  {
    heading: "Contacts & Docs",
    items: [
      { label: "Contacts", href: "/contacts", icon: "👤" },
      { label: "Companies", href: "/companies", icon: "🏢" },
      { label: "Documents", href: "/documents", icon: "📁" },
      { label: "Jurisdictions", href: "/jurisdictions", icon: "🌍" },
    ],
  },
  {
    heading: "Platform",
    items: [
      { label: "Sign Auth", href: "/signature-authority", icon: "🔐" },
      { label: "Billing", href: "/billing", icon: "💳" },
      { label: "Translations", href: "/i18n", icon: "🌐" },
      { label: "Outbox", href: "/outbox", icon: "📡" },
      { label: "Observability", href: "/observability", icon: "🔭" },
      { label: "Data Governance", href: "/data-governance", icon: "🛡️" },
    ],
  },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export default function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    new Set(["Main", "Agreements", "Workflow"])
  );

  const toggleGroup = (heading: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(heading)) next.delete(heading);
      else next.add(heading);
      return next;
    });
  };

  return (
    <aside
      className={`
        relative flex flex-col h-full
        bg-white border-r border-gray-200
        transition-[width] duration-300 ease-in-out overflow-hidden
        ${collapsed ? "w-[60px]" : "w-[220px]"}
      `}
    >
      {/* Logo + toggle */}
      <div className="flex items-center justify-between h-14 px-3 border-b border-gray-100 shrink-0">
        {!collapsed && (
          <Link href="/dashboard" className="flex items-center gap-2">
            <span className="text-lg leading-none">⚖️</span>
            <span className="text-sm font-bold text-gray-900 whitespace-nowrap">ContractOS</span>
          </Link>
        )}
        {collapsed && (
          <Link href="/dashboard" className="mx-auto">
            <span className="text-lg leading-none">⚖️</span>
          </Link>
        )}
        <button
          onClick={onToggle}
          className={`ml-auto text-gray-400 hover:text-gray-700 transition-colors ${collapsed ? "mx-auto" : ""}`}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none">
              <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          ) : (
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none">
              <path d="M10 4L6 8l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </button>
      </div>

      {/* Nav groups */}
      <nav className="flex-1 overflow-y-auto py-3 space-y-0.5 scrollbar-thin">
        {NAV_GROUPS.map((group) => {
          const expanded = expandedGroups.has(group.heading);
          return (
            <div key={group.heading}>
              {/* Group heading (only when sidebar expanded) */}
              {!collapsed && (
                <button
                  onClick={() => toggleGroup(group.heading)}
                  className="w-full flex items-center justify-between px-3 py-1.5 text-left group"
                >
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 group-hover:text-gray-600 transition-colors">
                    {group.heading}
                  </span>
                  <svg
                    className={`w-3 h-3 text-gray-400 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
                    viewBox="0 0 16 16" fill="none"
                  >
                    <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              )}

              {/* Items */}
              {(collapsed || expanded) && (
                <div className={collapsed ? "space-y-0.5 px-1.5 py-0.5" : "space-y-0.5 px-2 pb-1"}>
                  {group.items.map((item) => {
                    const active = pathname === item.href || pathname?.startsWith(item.href + "/");
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        title={collapsed ? item.label : undefined}
                        className={`
                          flex items-center gap-2.5 rounded-lg px-2 py-1.5
                          text-sm font-medium transition-all duration-150
                          ${active
                            ? "bg-brand-50 text-brand-700"
                            : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                          }
                          ${collapsed ? "justify-center" : ""}
                        `}
                      >
                        <span className="text-base leading-none shrink-0">{item.icon}</span>
                        {!collapsed && (
                          <span className="truncate">{item.label}</span>
                        )}
                        {!collapsed && item.badge && (
                          <span className="ml-auto text-[10px] font-bold bg-rose-100 text-rose-700 rounded-full px-1.5 py-0.5">
                            {item.badge}
                          </span>
                        )}
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* User + sign out */}
      <div className="border-t border-gray-100 p-3 shrink-0 space-y-1">
        <Link
          href="/settings"
          title={collapsed ? "Settings" : undefined}
          className={`flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm text-gray-600 hover:bg-gray-50 hover:text-gray-900 transition-colors ${collapsed ? "justify-center" : ""}`}
        >
          <span className="text-base leading-none">⚙️</span>
          {!collapsed && <span className="truncate">Settings</span>}
        </Link>
        {!collapsed && user && (
          <div className="px-2 py-1 text-xs text-gray-500 truncate">{user.name}</div>
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
