"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  getRiskGraphStats,
  getHighRiskAgreements,
  getExpiringAgreements,
  getOpenObligations,
  askCopilot,
  CopilotAnswer,
} from "@/lib/api";

type Tab = "overview" | "high-risk" | "expiring" | "obligations";

interface HighRiskRow {
  agreement_id: string;
  title: string;
  edge_count: number;
  risk_score: number;
}

interface ExpiringRow {
  agreement_id: string;
  title: string;
  status: string;
  expiry_date: string | null;
  days_remaining: number | null;
}

const SEVERITY_STYLES: Record<string, string> = {
  high: "bg-red-100 text-red-800 border-red-200",
  medium: "bg-amber-100 text-amber-800 border-amber-200",
  low: "bg-emerald-100 text-emerald-800 border-emerald-200",
};

function riskClass(score: number): string {
  if (score >= 2) return SEVERITY_STYLES.high;
  if (score >= 1) return SEVERITY_STYLES.medium;
  return SEVERITY_STYLES.low;
}

export default function RiskPage() {
  const { token } = useAuth();
  const [tab, setTab] = useState<Tab>("overview");
  const [stats, setStats] = useState<{ nodes: number; edges: number } | null>(null);
  const [highRisk, setHighRisk] = useState<HighRiskRow[]>([]);
  const [expiring, setExpiring] = useState<ExpiringRow[]>([]);
  const [obligations, setObligations] = useState<
    Array<{
      agreement_id: string;
      agreement_title: string;
      counterparty: string | null;
      obligation: string;
      status: string;
      due_date: string | null;
    }>
  >([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [s, hr, ex, ob] = await Promise.all([
        getRiskGraphStats(token),
        getHighRiskAgreements(token),
        getExpiringAgreements(token, 90),
        getOpenObligations(token),
      ]);
      setStats(s);
      setHighRisk(hr || []);
      setExpiring(ex || []);
      setObligations(ob || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load risk data");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const handleAsk = async () => {
    if (!token || !question.trim()) return;
    setError(null);
    setAnswer(null);
    try {
      setAnswer(await askCopilot(token, question.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Copilot request failed");
    }
  };

  const tabs: Array<{ id: Tab; label: string }> = [
    { id: "overview", label: "Overview" },
    { id: "high-risk", label: `High risk (${highRisk.length})` },
    { id: "expiring", label: `Expiring (${expiring.length})` },
    { id: "obligations", label: `Open obligations (${obligations.length})` },
  ];

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Risk &amp; Compliance</h1>
          <p className="text-sm text-gray-500">
            Contract Risk Graph — live exposure across agreements
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded border px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <nav className="mb-6 flex gap-2 border-b">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm ${
              tab === t.id
                ? "border-b-2 border-blue-600 font-medium text-blue-700"
                : "text-gray-500 hover:text-gray-800"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "overview" && (
        <section className="space-y-6">
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded border p-4">
              <p className="text-sm text-gray-500">Graph nodes</p>
              <p className="text-2xl font-semibold">{stats?.nodes ?? "—"}</p>
            </div>
            <div className="rounded border p-4">
              <p className="text-sm text-gray-500">Risk edges</p>
              <p className="text-2xl font-semibold">{stats?.edges ?? "—"}</p>
            </div>
            <div className="rounded border p-4">
              <p className="text-sm text-gray-500">Expiring ≤ 90 days</p>
              <p className="text-2xl font-semibold">{expiring.length}</p>
            </div>
          </div>

          <div className="rounded border p-4">
            <h2 className="mb-2 font-medium">AI Copilot — ask about risk</h2>
            <div className="flex gap-2">
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAsk()}
                placeholder="e.g. Which agreements have unlimited liability?"
                className="flex-1 rounded border px-3 py-2 text-sm"
              />
              <button
                onClick={handleAsk}
                className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
              >
                Ask
              </button>
            </div>
            {answer && (
              <div className="mt-3 rounded bg-gray-50 p-3 text-sm">
                <p className="whitespace-pre-wrap">{answer.answer}</p>
                {answer.uncertainty && (
                  <p className="mt-2 text-xs text-amber-700">
                    Uncertainty: {answer.uncertainty}
                  </p>
                )}
                {answer.citations?.length > 0 && (
                  <ul className="mt-2 list-disc pl-5 text-gray-600">
                    {answer.citations.map((c, i) => (
                      <li key={i}>
                        <Link
                          className="text-blue-600 hover:underline"
                          href={`/agreements/${c.agreement_id}`}
                        >
                          {c.quote || "source"}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
                {answer.requires_human_review && (
                  <p className="mt-2 text-xs text-gray-500">
                    Requires human review before acting on this answer.
                  </p>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      {tab === "high-risk" && (
        <section className="space-y-2">
          {highRisk.length === 0 && (
            <p className="text-sm text-gray-500">No high-risk agreements.</p>
          )}
          {highRisk.map((a) => (
            <div
              key={a.agreement_id}
              className="flex items-center justify-between rounded border p-3"
            >
              <div>
                <Link
                  className="font-medium text-blue-600 hover:underline"
                  href={`/agreements/${a.agreement_id}`}
                >
                  {a.title}
                </Link>
                <span className="ml-2 text-sm text-gray-500">
                  {a.edge_count} open risk edges
                </span>
              </div>
              <span
                className={`rounded border px-2 py-0.5 text-sm ${riskClass(a.risk_score)}`}
              >
                risk {a.risk_score.toFixed(1)}
              </span>
            </div>
          ))}
        </section>
      )}

      {tab === "expiring" && (
        <section className="space-y-2">
          {expiring.length === 0 && (
            <p className="text-sm text-gray-500">Nothing expiring in the window.</p>
          )}
          {expiring.map((a) => (
            <div
              key={a.agreement_id}
              className="flex items-center justify-between rounded border p-3"
            >
              <div>
                <Link
                  className="font-medium text-blue-600 hover:underline"
                  href={`/agreements/${a.agreement_id}`}
                >
                  {a.title}
                </Link>
                <span className="ml-2 text-sm text-gray-500">
                  {a.expiry_date ?? "no expiry set"}
                </span>
              </div>
              <span
                className={`rounded border px-2 py-0.5 text-sm ${
                  (a.days_remaining ?? 999) <= 30
                    ? SEVERITY_STYLES.high
                    : SEVERITY_STYLES.medium
                }`}
              >
                {a.days_remaining != null ? `${a.days_remaining} days left` : "unknown"}
              </span>
            </div>
          ))}
        </section>
      )}

      {tab === "obligations" && (
        <section className="space-y-2">
          {obligations.length === 0 && (
            <p className="text-sm text-gray-500">No open obligations.</p>
          )}
          {obligations.map((o, i) => (
            <div key={`${o.agreement_id}-${i}`} className="rounded border p-3">
              <div className="flex items-center justify-between">
                <Link
                  className="font-medium text-blue-600 hover:underline"
                  href={`/agreements/${o.agreement_id}`}
                >
                  {o.counterparty ?? o.agreement_title}
                </Link>
                <span className="text-sm text-gray-600">{o.status}</span>
              </div>
              <p className="mt-1 text-sm text-gray-600">{o.obligation}</p>
              {o.due_date && (
                <p className="text-xs text-gray-500">due {o.due_date}</p>
              )}
            </div>
          ))}
        </section>
      )}
    </main>
  );
}
