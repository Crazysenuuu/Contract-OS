"use client";

import { useAuth } from "@/contexts/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import Link from "next/link";
import NotificationBell from "@/components/NotificationBell";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, isLoading, logout } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
    }
  }, [user, isLoading, router]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-500">Loading...</div>
      </div>
    );
  }

  if (!user) {
    return null;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between h-16">
            <div className="flex">
              <Link
                href="/dashboard"
                className="flex items-center px-2 py-2 text-gray-900 font-bold"
              >
                ContractOS
              </Link>

              <div className="hidden sm:ml-6 sm:flex sm:space-x-6">
                <Link
                  href="/dashboard"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-900"
                >
                  Dashboard
                </Link>
                <Link
                title="Search agreements"
                aria-label="Search agreements"
                href="/search"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">🔍</span> Search
                </Link>
                <Link
                  href="/agreements/new"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
                >
                  New Agreement
                </Link>
                <Link
                  href="/analytics"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
                >
                  Analytics
                </Link>
                <Link
                  href="/jurisdictions"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
                >
                  Jurisdictions
                </Link>
                <Link
                  href="/esignature"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
                >
                  E-Sign
                </Link>
                <Link
                  href="/bulk"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
                >
                  Bulk
                </Link>
                <Link
                  href="/clause-library"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
                >
                  Clauses
                </Link>
                <Link
                title="Signature authority"
                aria-label="Signature authority"
                href="/signature-authority"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">🔐</span> Sign Auth
                </Link>
                <Link
                title="Company policies"
                aria-label="Company policies"
                href="/company-policies"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">📋</span> Policies
                </Link>
                <Link
                title="Languages and translations"
                aria-label="Languages and translations"
                href="/i18n"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">🌐</span> Languages
                </Link>
                <Link
                title="Translation sync"
                aria-label="Translation sync"
                href="/translations"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">🔄</span> Sync
                </Link>
                <Link
                title="Translation queue"
                aria-label="Translation queue"
                href="/translation-queue"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">📋</span> Queue
                </Link>
                <Link
                title="Translation progress"
                aria-label="Translation progress"
                href="/translation-progress"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">📊</span> Progress
                </Link>
                <Link
                title="Audit trail"
                aria-label="Audit trail"
                href="/audit"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">🔗</span> Audit
                </Link>
                <Link
                title="Risk workspace"
                aria-label="Risk workspace"
                href="/risk"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">⚠️</span> Risk
                </Link>
                <Link
                title="Agreement execution"
                aria-label="Agreement execution"
                href="/execution"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">✍️</span> Execution
                </Link>
                <Link
                title="Legal knowledge base"
                aria-label="Legal knowledge base"
                href="/legal-knowledge"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">⚖️</span> Legal
                </Link>
                <Link
                title="Billing"
                aria-label="Billing"
                href="/billing"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">💳</span> Billing
                </Link>
                <Link
                title="Notification outbox"
                aria-label="Notification outbox"
                href="/outbox"
                className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-500 hover:text-gray-900"
              >
                <span aria-hidden="true">📡</span> Outbox
                </Link>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-4">
              <span className="text-sm text-gray-700">{user.name}</span>
              <Link
                href="/settings"
                title="Settings & Security"
                aria-label="Settings and security"
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                <span aria-hidden="true">⚙️</span>
              </Link>
              <NotificationBell />
              <Link
                href="/settings/alerting"
                title="Alert settings"
                aria-label="Alert settings"
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                <span aria-hidden="true">🚨</span>
              </Link>
              <Link
                href="/settings/organization"
                title="Organization settings"
                aria-label="Organization settings"
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                <span aria-hidden="true">🏢</span>
              </Link>
              <Link
                href="/settings/branding"
                title="Branding settings"
                aria-label="Branding settings"
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                <span aria-hidden="true">🎨</span>
              </Link>
              <Link
                href="/settings/webhooks"
                title="Webhook settings"
                aria-label="Webhook settings"
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                <span aria-hidden="true">🔗</span>
              </Link>
              <Link
                href="/observability"
                title="Observability"
                aria-label="Observability"
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                <span aria-hidden="true">📈</span>
              </Link>
              <Link
                href="/data-governance"
                title="Data governance"
                aria-label="Data governance"
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                <span aria-hidden="true">🛡️</span>
              </Link>
              <button
                onClick={logout}
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        {children}
      </main>
    </div>
  );
}
