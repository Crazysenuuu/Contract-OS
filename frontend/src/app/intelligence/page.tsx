"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import {
  CopilotAnswer,
  ConversationDetail,
  ConversationMessage,
  ConversationSummary,
  FeedbackRating,
  FeedbackReviewRow,
  OpenObligationRow,
  askConversation,
  askCopilot,
  createConversation,
  getConversation,
  getExpiringAgreements,
  getHighRiskAgreements,
  getOpenObligations,
  getRiskGraphStats,
  listAgreements,
  listConversations,
  listGovernanceFeedback,
  promoteFeedback,
  rateMessage,
} from "@/lib/api";

const RATING_LABELS: Record<string, string> = {
  helpful: "👍 Helpful",
  not_helpful: "👎 Not helpful",
  incorrect: "✗ Incorrect",
  missing_source: "◌ Missing source",
  wrong_source: "→ Wrong source",
  incomplete: "… Incomplete",
};

const NEGATIVE_RATINGS: FeedbackRating[] = [
  "incorrect",
  "missing_source",
  "wrong_source",
  "incomplete",
  "not_helpful",
];

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function IntelligencePage() {
  const { token, user } = useAuth();

  // Governance review is admin-only (backend enforces 403; we hide it so
  // regular members never see a section they cannot use).
  const isAdmin = user?.is_admin === true;

  // --- Conversations (spec 2.10.36) ---
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [thread, setThread] = useState<ConversationDetail | null>(null);
  const [threadQuestion, setThreadQuestion] = useState("");
  const [threadAsking, setThreadAsking] = useState(false);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [feedbackFor, setFeedbackFor] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  // --- Copilot (one-shot) ---
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null);
  const [agreements, setAgreements] = useState<Array<{ id: string; title: string }>>(
    []
  );
  const [scopeAgreement, setScopeAgreement] = useState("");

  // --- Governance: feedback review queue (spec 2.10.39) ---
  const [feedbackRows, setFeedbackRows] = useState<FeedbackReviewRow[]>([]);
  const [pendingOnly, setPendingOnly] = useState(true);
  const [promotingId, setPromotingId] = useState<string | null>(null);

  // --- Risk graph overview ---
  const [stats, setStats] = useState<{ nodes: number; edges: number; by_type: Record<string, number> } | null>(null);
  const [highRisk, setHighRisk] = useState<Array<{ agreement_id: string; title: string; risk_score: number }>>([]);
  const [openObligations, setOpenObligations] = useState<OpenObligationRow[]>([]);
  const [expiring, setExpiring] = useState<Array<{ agreement_id: string; title: string; days_remaining: number | null }>>([]);
  const [error, setError] = useState("");

  const refreshConversations = useCallback(async () => {
    if (!token) return;
    try {
      setConversations(await listConversations(token));
    } catch {
      // Sidebar is best-effort; the rest of the page still works.
    }
  }, [token]);

  const refreshFeedback = useCallback(async () => {
    if (!token || !isAdmin) return;
    try {
      setFeedbackRows(
        await listGovernanceFeedback(token, { pendingOnly, limit: 50 })
      );
    } catch {
      // A 403 (or transient failure) simply hides the queue contents.
    }
  }, [token, pendingOnly, isAdmin]);

  useEffect(() => {
    if (!token) return;
    refreshConversations();
    refreshFeedback();
    listAgreements(token).then(setAgreements).catch(() => {});
    getRiskGraphStats(token).then(setStats).catch(() => {});
    getHighRiskAgreements(token).then(setHighRisk).catch(() => {});
    getOpenObligations(token).then(setOpenObligations).catch(() => {});
    getExpiringAgreements(token, 90).then(setExpiring).catch(() => {});
  }, [token, refreshConversations, refreshFeedback]);

  const openConversation = useCallback(
    async (id: string) => {
      if (!token) return;
      setActiveConversationId(id);
      setThread(null);
      setFeedbackFor(null);
      setNotice("");
      try {
        setThread(await getConversation(token, id));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load conversation");
      }
    },
    [token]
  );

  const newConversation = useCallback(async () => {
    if (!token) return;
    setCreatingConversation(true);
    setError("");
    try {
      const created = await createConversation(token, {
        agreementId: scopeAgreement || undefined,
        title: undefined,
      }) as ConversationSummary;
      await refreshConversations();
      await openConversation(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start conversation");
    } finally {
      setCreatingConversation(false);
    }
  }, [token, scopeAgreement, refreshConversations, openConversation]);

  const askInThread = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!token || !activeConversationId || !threadQuestion.trim()) return;
      setThreadAsking(true);
      setError("");
      try {
        await askConversation(token, activeConversationId, threadQuestion.trim());
        setThreadQuestion("");
        // Reload canonical history (includes both turns + feedback state).
        setThread(await getConversation(token, activeConversationId));
        refreshConversations();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Request failed");
      } finally {
        setThreadAsking(false);
      }
    },
    [token, activeConversationId, threadQuestion, refreshConversations]
  );

  const submitRating = useCallback(
    async (messageId: string, rating: FeedbackRating) => {
      if (!token) return;
      setFeedbackFor(null);
      setNotice("");
      try {
        await rateMessage(token, messageId, rating);
        setNotice(`Thanks — recorded "${RATING_LABELS[rating] ?? rating}".`);
        if (activeConversationId) {
          setThread(await getConversation(token, activeConversationId));
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Could not record feedback";
        setNotice(msg.includes("already recorded") ? "Already rated." : msg);
      }
    },
    [token, activeConversationId]
  );

  const promoteRow = useCallback(
    async (feedbackId: string) => {
      if (!token || !isAdmin) return;
      setPromotingId(feedbackId);
      setError("");
      try {
        const res = await promoteFeedback(token, feedbackId);
        setNotice(`Promoted to evaluation dataset (${res.category}).`);
        await refreshFeedback();
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : "Could not promote feedback";
        setError(msg.includes("already promoted") ? "Already promoted." : msg);
      } finally {
        setPromotingId(null);
      }
    },
    [token, refreshFeedback]
  );

  const submitQuestion = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!token || !question.trim()) return;
      setAsking(true);
      setError("");
      setAnswer(null);
      try {
        const res = await askCopilot(
          token,
          question.trim(),
          scopeAgreement || undefined
        );
        setAnswer(res);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Request failed");
      } finally {
        setAsking(false);
      }
    },
    [token, question, scopeAgreement]
  );

  const statusBadge = (status?: string | null) => {
    const map: Record<string, string> = {
      ANSWERED: "bg-green-100 text-green-800",
      INSUFFICIENT_EVIDENCE: "bg-yellow-100 text-yellow-800",
      REQUIRES_HUMAN_REVIEW: "bg-orange-100 text-orange-800",
    };
    return (
      <span
        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
          map[status || ""] || "bg-gray-100 text-gray-700"
        }`}
      >
        {status || "—"}
      </span>
    );
  };

  const renderCitations = (citations: ConversationMessage["citations"]) =>
    citations.length > 0 ? (
      <div className="mt-2 rounded border border-gray-200 bg-gray-50 p-3 space-y-2">
        <p className="text-xs font-medium text-gray-600">Cited evidence</p>
        {citations.map((c) => (
          <blockquote
            key={c.source_number}
            className="border-l-2 border-blue-300 pl-3 text-xs text-gray-600"
          >
            [{c.source_number}] “…{c.quote}…”
          </blockquote>
        ))}
      </div>
    ) : null;

  const renderFeedbackRow = (m: ConversationMessage) => {
    if (m.role !== "assistant") return null;
    if (m.feedback_rating) {
      return (
        <p className="mt-2 text-xs text-gray-500">
          Your rating: {RATING_LABELS[m.feedback_rating] ?? m.feedback_rating}
        </p>
      );
    }
    if (feedbackFor === m.id) {
      return (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-gray-500">What was wrong?</span>
          {NEGATIVE_RATINGS.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => submitRating(m.id, r)}
              className="rounded-full border border-gray-300 px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-100"
            >
              {RATING_LABELS[r]}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setFeedbackFor(null)}
            className="text-xs text-gray-400 hover:text-gray-600"
          >
            cancel
          </button>
        </div>
      );
    }
    return (
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          title="Helpful"
          onClick={() => submitRating(m.id, "helpful")}
          className="text-sm text-gray-400 hover:text-green-700"
        >
          👍
        </button>
        <button
          type="button"
          title="Something was wrong"
          onClick={() => setFeedbackFor(m.id)}
          className="text-sm text-gray-400 hover:text-red-700"
        >
          👎
        </button>
        <span className="text-xs text-gray-400">
          {statusBadge(m.answer_status)}
        </span>
      </div>
    );
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">
          Contract Intelligence
        </h1>
        <p className="text-sm text-gray-500">
          AI Copilot answers grounded in your contracts, plus portfolio risk
          signals from the Contract Risk Graph.
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-800">
          {notice}
        </div>
      )}

      {/* ---------- AI Copilot: conversations + thread ---------- */}
      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="font-medium text-gray-800">AI Copilot</h2>
          <p className="text-xs text-gray-500">
            Answers cite the exact contract passages they are drawn from — no
            invented terms. Every turn is re-checked against your current
            access rights.
          </p>
        </div>

        <div className="flex flex-col md:flex-row">
          {/* Conversations sidebar */}
          <aside className="md:w-72 md:border-r border-b md:border-b-0 border-gray-200 p-3">
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-gray-700">
                Conversations
              </p>
              <button
                type="button"
                onClick={newConversation}
                disabled={creatingConversation}
                className="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {creatingConversation ? "…" : "+ New"}
              </button>
            </div>
            <select
              value={scopeAgreement}
              onChange={(e) => setScopeAgreement(e.target.value)}
              className="mb-3 w-full rounded border border-gray-300 px-2 py-1.5 text-xs"
            >
              <option value="">New chats: all accessible contracts</option>
              {agreements.map((a) => (
                <option key={a.id} value={a.id}>
                  Scope: {a.title}
                </option>
              ))}
            </select>
            {conversations.length === 0 ? (
              <p className="text-xs text-gray-400">
                No conversations yet — start one with “+ New”.
              </p>
            ) : (
              <ul className="space-y-1">
                {conversations.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => openConversation(c.id)}
                      className={`w-full rounded px-2.5 py-2 text-left text-sm ${
                        activeConversationId === c.id
                          ? "bg-blue-50 text-blue-900"
                          : "hover:bg-gray-50 text-gray-700"
                      }`}
                    >
                      <span className="block truncate font-medium">
                        {c.title || "Untitled conversation"}
                      </span>
                      <span className="block text-xs text-gray-400">
                        {c.message_count} messages · {fmtTime(c.last_message_at || c.created_at)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </aside>

          {/* Thread / quick ask */}
          <div className="flex-1">
            {thread ? (
              <div className="flex flex-col">
                <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2">
                  <p className="text-sm font-medium text-gray-700 truncate">
                    {thread.title || "Untitled conversation"}
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setActiveConversationId(null);
                      setThread(null);
                      setFeedbackFor(null);
                    }}
                    className="text-xs text-gray-400 hover:text-gray-600"
                  >
                    close
                  </button>
                </div>
                <div className="max-h-[28rem] space-y-3 overflow-y-auto p-4">
                  {thread.messages.length === 0 && (
                    <p className="text-sm text-gray-400">
                      Ask the first question below.
                    </p>
                  )}
                  {thread.messages.map((m) => (
                    <div
                      key={m.id}
                      className={
                        m.role === "user"
                          ? "ml-auto max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white"
                          : "mr-auto max-w-[85%] rounded-lg border border-gray-200 bg-gray-50 px-3 py-2"
                      }
                    >
                      <p className="whitespace-pre-wrap text-sm leading-relaxed">
                        {m.content}
                      </p>
                      {m.role === "assistant" && (
                        <>
                          <div className="mt-1 flex items-center gap-2 text-xs text-gray-400">
                            {m.answer_status && statusBadge(m.answer_status)}
                            {m.latency_ms != null && <span>{m.latency_ms} ms</span>}
                          </div>
                          {renderCitations(m.citations)}
                          {renderFeedbackRow(m)}
                        </>
                      )}
                    </div>
                  ))}
                </div>
                <form
                  onSubmit={askInThread}
                  className="flex gap-2 border-t border-gray-200 p-3"
                >
                  <input
                    value={threadQuestion}
                    onChange={(e) => setThreadQuestion(e.target.value)}
                    placeholder="Ask a follow-up…"
                    className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
                  />
                  <button
                    type="submit"
                    disabled={threadAsking || !threadQuestion.trim()}
                    className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {threadAsking ? "Analyzing…" : "Ask"}
                  </button>
                </form>
              </div>
            ) : (
              <form onSubmit={submitQuestion} className="space-y-3 p-4">
                <select
                  value={scopeAgreement}
                  onChange={(e) => setScopeAgreement(e.target.value)}
                  className="w-full sm:w-80 rounded border border-gray-300 px-3 py-2 text-sm"
                >
                  <option value="">Ask across all accessible contracts</option>
                  {agreements.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.title}
                    </option>
                  ))}
                </select>
                <div className="flex gap-2">
                  <input
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder='e.g. "Which suppliers carry unlimited liability?" or "What is our auto-renewal notice period?"'
                    className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
                  />
                  <button
                    type="submit"
                    disabled={asking || !question.trim()}
                    className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {asking ? "Analyzing…" : "Ask"}
                  </button>
                </div>
                <p className="text-xs text-gray-400">
                  One-off question. Use “+ New” for a conversation with history.
                </p>
              </form>
            )}
          </div>
        </div>

        {answer && !thread && (
          <div className="border-t border-gray-200 p-4 space-y-3">
            <div className="flex items-center gap-2">
              {statusBadge(answer.status)}
              <span className="text-xs text-gray-500">
                intent: {answer.intent} · evidence: {answer.evidence_count} ·{" "}
                uncertainty: {answer.uncertainty || "n/a"}
              </span>
            </div>
            <p className="text-sm text-gray-800 leading-relaxed">
              {answer.answer}
            </p>
            {answer.requires_human_review && (
              <div className="rounded border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-800">
                A legal professional should review the source clauses before
                acting on this answer.
              </div>
            )}
            {renderCitations(answer.citations)}
            {answer.suggested_actions.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {answer.suggested_actions.map((a) => (
                  <span
                    key={a}
                    className="rounded-full border border-gray-300 px-3 py-1 text-xs text-gray-600"
                  >
                    {a.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* ---------- Governance: feedback review (admin only) ---------- */}
      {isAdmin && (
      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <div>
            <h2 className="font-medium text-gray-800">Feedback review</h2>
            <p className="text-xs text-gray-500">
              User ratings awaiting review. Promoting adds a de-identified
              example to the evaluation dataset — it never reconfigures
              production AI directly (spec 2.10.39).
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={pendingOnly}
              onChange={(e) => setPendingOnly(e.target.checked)}
            />
            Pending only
          </label>
        </div>
        {feedbackRows.length === 0 ? (
          <p className="p-4 text-sm text-gray-400">
            {pendingOnly
              ? "No feedback awaiting review."
              : "No feedback recorded yet."}
          </p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {feedbackRows.map((row) => (
              <li key={row.id} className="space-y-2 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
                    {RATING_LABELS[row.rating] ?? row.rating}
                  </span>
                  {row.promoted ? (
                    <span className="rounded-full bg-green-50 px-2 py-0.5 text-xs text-green-700">
                      promoted
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => promoteRow(row.id)}
                      disabled={promotingId === row.id}
                      className="rounded bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {promotingId === row.id ? "Promoting…" : "Promote to eval"}
                    </button>
                  )}
                  <span className="text-xs text-gray-400">
                    {row.conversation_title || "Untitled"} · {fmtTime(row.created_at)}
                  </span>
                </div>
                {row.reason && (
                  <p className="text-xs text-gray-600">Reason: {row.reason}</p>
                )}
                <div className="rounded border border-gray-200 p-2.5 text-xs">
                  <p className="text-gray-800">
                    <span className="font-medium text-gray-500">Q:</span>{" "}
                    {row.question}
                  </p>
                  <p className="mt-1 text-gray-600">
                    <span className="font-medium text-gray-400">A:</span>{" "}
                    {row.answer}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      )}

      {/* ---------- Risk graph overview ---------- */}
      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="font-medium text-gray-800">Portfolio risk signals</h2>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 p-4">
          <div className="rounded border border-gray-200 p-3">
            <p className="text-xs text-gray-500">Graph nodes</p>
            <p className="text-xl font-semibold">{stats?.nodes ?? "—"}</p>
          </div>
          <div className="rounded border border-gray-200 p-3">
            <p className="text-xs text-gray-500">Relationships</p>
            <p className="text-xl font-semibold">{stats?.edges ?? "—"}</p>
          </div>
          <div className="rounded border border-gray-200 p-3">
            <p className="text-xs text-gray-500">High-risk agreements</p>
            <p className="text-xl font-semibold">{highRisk.length}</p>
          </div>
          <div className="rounded border border-gray-200 p-3">
            <p className="text-xs text-gray-500">Expiring ≤ 90 days</p>
            <p className="text-xl font-semibold">{expiring.length}</p>
          </div>
        </div>
        <div className="grid md:grid-cols-2 gap-4 p-4 border-t border-gray-200">
          <div>
            <p className="text-sm font-medium text-gray-700 mb-2">
              Highest risk agreements
            </p>
            {highRisk.length === 0 ? (
              <p className="text-sm text-gray-400">
                No scored agreements yet — open an agreement and build its risk
                graph.
              </p>
            ) : (
              <ul className="space-y-2">
                {highRisk.slice(0, 6).map((r) => (
                  <li
                    key={r.agreement_id}
                    className="flex justify-between rounded border border-gray-200 px-3 py-2 text-sm"
                  >
                    <span className="truncate pr-2">{r.title}</span>
                    <span className="font-medium text-red-700">
                      {r.risk_score}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <p className="text-sm font-medium text-gray-700 mb-2">
              Open obligations
            </p>
            {openObligations.length === 0 ? (
              <p className="text-sm text-gray-400">No open obligations.</p>
            ) : (
              <ul className="space-y-2">
                {openObligations.slice(0, 6).map((o, i) => (
                  <li
                    key={i}
                    className="rounded border border-gray-200 px-3 py-2 text-sm"
                  >
                    <span className="font-medium">{o.obligation}</span>
                    <span className="ml-2 text-xs text-gray-500">
                      {o.counterparty || "—"} · {o.status}
                      {o.due_date ? ` · due ${o.due_date}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        {expiring.length > 0 && (
          <div className="p-4 border-t border-gray-200">
            <p className="text-sm font-medium text-gray-700 mb-2">
              Expiring soon
            </p>
            <div className="flex flex-wrap gap-2">
              {expiring.map((e) => (
                <span
                  key={e.agreement_id}
                  className="rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-800"
                >
                  {e.title} · {e.days_remaining ?? "?"} days left
                </span>
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
