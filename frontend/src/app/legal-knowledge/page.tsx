"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listAgreements,
  listLegalSources,
  LegalSource,
  createLegalSource,
  approveLegalSource,
  rejectLegalSource,
  listLegalRules,
  LegalRule,
  createLegalRule,
  approveLegalRule,
  retireLegalRule,
  validateAgreementLegal,
  LegalFinding,
} from "@/lib/api";

interface Agreement {
  id: string;
  title: string;
  status: string;
  created_at: string;
}

export default function LegalKnowledgePage() {
  const { token, user } = useAuth();
  const isAdmin = !!user?.is_admin;
  const [agreements, setAgreements] = useState<Agreement[]>([]);
  const [tab, setTab] = useState<"sources" | "rules" | "validate">("sources");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Sources
  const [sources, setSources] = useState<LegalSource[]>([]);
  const [srcTitle, setSrcTitle] = useState("");
  const [srcType, setSrcType] = useState("statute");
  const [srcJurisdiction, setSrcJurisdiction] = useState("LK");
  const [srcUrl, setSrcUrl] = useState("");
  const [srcContent, setSrcContent] = useState("");

  // Rules
  const [rules, setRules] = useState<LegalRule[]>([]);
  const [ruleStatusFilter, setRuleStatusFilter] = useState("active");
  const [ruleKey, setRuleKey] = useState("");
  const [ruleTitle, setRuleTitle] = useState("");
  const [ruleJurisdiction, setRuleJurisdiction] = useState("LK");
  const [ruleProposition, setRuleProposition] = useState("");
  const [ruleSourceId, setRuleSourceId] = useState("");
  const [ruleSourceVersionId, setRuleSourceVersionId] = useState("");
  const [ruleSeverity, setRuleSeverity] = useState("warning");
  const [ruleAppliesTo, setRuleAppliesTo] = useState("");
  const [ruleCondition, setRuleCondition] = useState("");

  // Validate
  const [validationAgreement, setValidationAgreement] = useState("");
  const [validation, setValidation] = useState<{
    agreement_id: string;
    governing_law: string | null;
    blocking_count: number;
    finding_count: number;
    can_proceed: boolean;
    findings: LegalFinding[];
  } | null>(null);

  useEffect(() => {
    if (token) {
      listAgreements(token).then(setAgreements).catch(console.error);
    }
  }, [token]);

  const reloadSources = useCallback(() => {
    if (!token) return;
    listLegalSources(token)
      .then(setSources)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load sources"));
  }, [token]);

  const reloadRules = useCallback(() => {
    if (!token) return;
    listLegalRules(token, { status: ruleStatusFilter })
      .then(setRules)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load rules"));
  }, [token, ruleStatusFilter]);

  useEffect(() => {
    reloadSources();
  }, [reloadSources]);

  useEffect(() => {
    reloadRules();
  }, [reloadRules]);

  const run = async (fn: () => Promise<unknown>, successMsg: string, after?: () => void) => {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(successMsg);
      after?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    }
  };

  const selectedSource = sources.find((s) => s.id === ruleSourceId);

  const handleCreateSource = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !srcTitle.trim() || !srcContent.trim()) return;
    run(
      () =>
        createLegalSource(token, {
          title: srcTitle.trim(),
          source_type: srcType,
          jurisdiction_code: srcJurisdiction,
          content_text: srcContent.trim(),
          url: srcUrl.trim() || undefined,
        }),
      "Source ingested — pending human review"
    ).then(() => {
      setSrcTitle("");
      setSrcUrl("");
      setSrcContent("");
      reloadSources();
    });
  };

  const handleCreateRule = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !ruleKey.trim() || !ruleTitle.trim() || !ruleProposition.trim() || !ruleSourceId) return;
    let condition: Record<string, unknown> | undefined;
    if (ruleCondition.trim()) {
      try {
        condition = JSON.parse(ruleCondition);
      } catch {
        setError("Executable condition must be valid JSON");
        return;
      }
    }
    run(
      () =>
        createLegalRule(token, {
          rule_key: ruleKey.trim(),
          title: ruleTitle.trim(),
          jurisdiction_code: ruleJurisdiction,
          proposition: ruleProposition.trim(),
          source_id: ruleSourceId,
          source_version_id: ruleSourceVersionId,
          executable_condition: condition,
          applies_to_agreement_types: ruleAppliesTo
            ? ruleAppliesTo.split(",").map((s) => s.trim()).filter(Boolean)
            : undefined,
          severity: ruleSeverity,
        }),
      "Rule created — pending human review"
    ).then(() => {
      setRuleKey("");
      setRuleTitle("");
      setRuleProposition("");
      setRuleCondition("");
      reloadRules();
    });
  };

  const handleValidate = async () => {
    if (!token || !validationAgreement) return;
    setError(null);
    try {
      const result = await validateAgreementLegal(token, validationAgreement);
      setValidation(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed");
    }
  };

  const severityColor = (severity: string) =>
    severity === "blocking"
      ? "bg-rose-100 text-rose-800 border-rose-200"
      : severity === "warning"
        ? "bg-amber-100 text-amber-800 border-amber-200"
        : "bg-blue-100 text-blue-800 border-blue-200";

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        <div className="mb-6">
          <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
            ← Dashboard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 mt-1">Legal Knowledge Engine</h1>
          <p className="text-sm text-gray-500 mt-1">
            Sources and rules with human review gates (spec 1.10) — nothing activates without an
            admin review, and every rule cites its source.
          </p>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-md bg-rose-50 border border-rose-200 text-sm text-rose-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-4 px-4 py-3 rounded-md bg-emerald-50 border border-emerald-200 text-sm text-emerald-800">
            {notice}
          </div>
        )}

        <div className="flex gap-1 bg-gray-100 p-1 rounded-xl mb-6 w-max">
          {(["sources", "rules", "validate"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-xs font-bold rounded-lg transition-all ${
                tab === t
                  ? "bg-gradient-to-r from-indigo-600 to-purple-500 text-white shadow-md"
                  : "text-gray-500 hover:text-gray-800"
              }`}
            >
              {t === "sources" ? "Legal Sources" : t === "rules" ? "Rules" : "Validate Agreement"}
            </button>
          ))}
        </div>

        {tab === "sources" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-medium text-gray-900">Sources ({sources.length})</h2>
              </div>
              {sources.length === 0 ? (
                <p className="px-6 py-12 text-sm text-gray-500 text-center">
                  No legal sources yet. Ingest one on the right.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Title</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Jurisdiction</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                        <th className="px-6 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Review</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {sources.map((s) => (
                        <tr key={s.id} className="hover:bg-gray-50">
                          <td className="px-6 py-4">
                            <div className="text-sm font-bold text-gray-900">{s.title}</div>
                            <div className="text-xs text-gray-500">
                              {s.source_type}
                              {s.source_version ? ` · v${s.source_version}` : ""}
                            </div>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-600">{s.jurisdiction_code}</td>
                          <td className="px-6 py-4">
                            <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border ${
                              s.status === "active"
                                ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                                : s.status === "pending_review"
                                  ? "bg-amber-100 text-amber-800 border-amber-200"
                                  : "bg-gray-100 text-gray-600 border-gray-200"
                            }`}>
                              {s.status}
                            </span>
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex justify-end gap-2">
                              {s.status === "pending_review" && isAdmin && (
                                <>
                                  <button
                                    onClick={() =>
                                      run(
                                        () => approveLegalSource(token!, s.id),
                                        "Source activated",
                                        reloadSources
                                      )
                                    }
                                    className="px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700"
                                  >
                                    Approve
                                  </button>
                                  <button
                                    onClick={() =>
                                      run(
                                        () => rejectLegalSource(token!, s.id),
                                        "Source rejected",
                                        reloadSources
                                      )
                                    }
                                    className="px-3 py-1.5 text-xs font-bold rounded-lg bg-rose-100 text-rose-700 hover:bg-rose-200"
                                  >
                                    Reject
                                  </button>
                                </>
                              )}
                              {s.status === "pending_review" && !isAdmin && (
                                <span className="text-xs text-gray-400">needs admin review</span>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="bg-white shadow rounded-lg p-6 h-fit">
              <h2 className="text-lg font-medium text-gray-900 mb-4">Ingest Source</h2>
              <form onSubmit={handleCreateSource} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Title *</label>
                  <input
                    value={srcTitle}
                    onChange={(e) => setSrcTitle(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Type</label>
                    <select
                      value={srcType}
                      onChange={(e) => setSrcType(e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    >
                      <option value="statute">statute</option>
                      <option value="regulation">regulation</option>
                      <option value="case_law">case_law</option>
                      <option value="official_guidance">official_guidance</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Jurisdiction</label>
                    <input
                      value={srcJurisdiction}
                      onChange={(e) => setSrcJurisdiction(e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Source URL</label>
                  <input
                    value={srcUrl}
                    onChange={(e) => setSrcUrl(e.target.value)}
                    placeholder="https://…"
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Content text *</label>
                  <textarea
                    value={srcContent}
                    onChange={(e) => setSrcContent(e.target.value)}
                    rows={6}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <button
                  type="submit"
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-700"
                >
                  Ingest source
                </button>
              </form>
              <p className="mt-3 text-xs text-gray-400">
                Sources are created pending review and never auto-activate. Hashing captures the
                content for provenance.
              </p>
            </div>
          </div>
        )}

        {tab === "rules" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
                <h2 className="text-lg font-medium text-gray-900">Rules ({rules.length})</h2>
                <select
                  value={ruleStatusFilter}
                  onChange={(e) => setRuleStatusFilter(e.target.value)}
                  className="rounded-md border-gray-300 shadow-sm text-sm"
                >
                  <option value="active">active</option>
                  <option value="pending_review">pending_review</option>
                  <option value="retired">retired</option>
                  <option value="rejected">rejected</option>
                </select>
              </div>
              {rules.length === 0 ? (
                <p className="px-6 py-12 text-sm text-gray-500 text-center">No rules in this state.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Rule</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Jurisdiction</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Severity</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                        <th className="px-6 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Review</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {rules.map((r) => (
                        <tr key={r.id} className="hover:bg-gray-50">
                          <td className="px-6 py-4">
                            <div className="text-sm font-bold text-gray-900">
                              {r.title}
                              <span className="ml-2 text-xs font-mono text-gray-400">{r.rule_key}</span>
                            </div>
                            <div className="text-xs text-gray-500 mt-0.5 max-w-md line-clamp-2">
                              {r.proposition}
                            </div>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-600">{r.jurisdiction_code}</td>
                          <td className="px-6 py-4">
                            <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border ${severityColor(r.severity)}`}>
                              {r.severity}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-600">{r.status}</td>
                          <td className="px-6 py-4">
                            <div className="flex justify-end gap-2">
                              {r.status === "pending_review" && isAdmin && (
                                <button
                                  onClick={() =>
                                    run(
                                      () => approveLegalRule(token!, r.id),
                                      "Rule activated",
                                      reloadRules
                                    )
                                  }
                                  className="px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700"
                                >
                                  Approve
                                </button>
                              )}
                              {r.status === "active" && isAdmin && (
                                <button
                                  onClick={() =>
                                    run(
                                      () => retireLegalRule(token!, r.id),
                                      "Rule retired",
                                      reloadRules
                                    )
                                  }
                                  className="px-3 py-1.5 text-xs font-bold rounded-lg bg-gray-200 text-gray-700 hover:bg-gray-300"
                                >
                                  Retire
                                </button>
                              )}
                              {r.status === "pending_review" && !isAdmin && (
                                <span className="text-xs text-gray-400">needs admin review</span>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="bg-white shadow rounded-lg p-6 h-fit">
              <h2 className="text-lg font-medium text-gray-900 mb-4">New Rule</h2>
              <form onSubmit={handleCreateRule} className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Rule key *</label>
                    <input
                      value={ruleKey}
                      onChange={(e) => setRuleKey(e.target.value)}
                      placeholder="lk_nda_max_term"
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm font-mono"
                      required
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Jurisdiction</label>
                    <input
                      value={ruleJurisdiction}
                      onChange={(e) => setRuleJurisdiction(e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Title *</label>
                  <input
                    value={ruleTitle}
                    onChange={(e) => setRuleTitle(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Proposition *</label>
                  <textarea
                    value={ruleProposition}
                    onChange={(e) => setRuleProposition(e.target.value)}
                    rows={2}
                    placeholder="Non-compete terms may not exceed 2 years"
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Source *</label>
                  <select
                    value={ruleSourceId}
                    onChange={(e) => {
                      setRuleSourceId(e.target.value);
                      setRuleSourceVersionId("");
                    }}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  >
                    <option value="">Select source…</option>
                    {sources.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.title} ({s.jurisdiction_code})
                      </option>
                    ))}
                  </select>
                </div>
                {selectedSource && selectedSource.versions.length > 0 && (
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Source version *</label>
                    <select
                      value={ruleSourceVersionId}
                      onChange={(e) => setRuleSourceVersionId(e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                      required
                    >
                      <option value="">Select version…</option>
                      {selectedSource.versions.map((v) => (
                        <option key={v.id} value={v.id}>
                          v{v.version_number} ({v.status})
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Severity</label>
                    <select
                      value={ruleSeverity}
                      onChange={(e) => setRuleSeverity(e.target.value)}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    >
                      <option value="warning">warning</option>
                      <option value="blocking">blocking</option>
                      <option value="info">info</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Applies to types</label>
                    <input
                      value={ruleAppliesTo}
                      onChange={(e) => setRuleAppliesTo(e.target.value)}
                      placeholder="nda, employment (comma separated)"
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Executable condition (JSON)
                  </label>
                  <textarea
                    value={ruleCondition}
                    onChange={(e) => setRuleCondition(e.target.value)}
                    rows={3}
                    placeholder='{"op": "gte", "field": "term_months", "value": 24}'
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm font-mono"
                  />
                  <p className="mt-1 text-[11px] text-gray-400">
                    Optional. Safe declarative evaluator — whitelisted ops only, no code.
                  </p>
                </div>
                <button
                  type="submit"
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-700"
                >
                  Create rule
                </button>
              </form>
              <p className="mt-3 text-xs text-gray-400">
                Rules require a source citation and only activate after admin review.
              </p>
            </div>
          </div>
        )}

        {tab === "validate" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-medium text-gray-900">Validation Findings</h2>
              </div>
              {validation ? (
                <div className="p-6">
                  <div
                    className={`mb-4 px-4 py-3 rounded-md border text-sm ${
                      validation.can_proceed
                        ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                        : "bg-rose-50 border-rose-200 text-rose-800"
                    }`}
                  >
                    <strong>{validation.can_proceed ? "Can proceed" : "Blocked"}</strong> —{" "}
                    {validation.blocking_count} blocking / {validation.finding_count} total findings
                    {validation.governing_law ? ` under ${validation.governing_law} law` : ""}.
                  </div>
                  {validation.findings.length === 0 ? (
                    <p className="text-sm text-gray-500">
                      No findings — this agreement passes all active rules.
                    </p>
                  ) : (
                    <ul className="space-y-3">
                      {validation.findings.map((f) => (
                        <li key={f.rule_id} className="border border-gray-200 rounded-lg p-4">
                          <div className="flex items-center justify-between">
                            <div className="text-sm font-bold text-gray-900">{f.title}</div>
                            <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border ${severityColor(f.severity)}`}>
                              {f.severity}
                            </span>
                          </div>
                          <div className="text-xs text-gray-600 mt-1">{f.message}</div>
                          <div className="text-xs text-gray-400 mt-2 font-mono">{f.rule_key}</div>
                          {f.source?.jurisdiction_code && (
                            <div className="text-[11px] text-gray-400 mt-0.5">
                              Source: {f.source.jurisdiction_code}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <p className="px-6 py-12 text-sm text-gray-500 text-center">
                  Select an agreement and run validation.
                </p>
              )}
            </div>

            <div className="bg-white shadow rounded-lg p-6 h-fit">
              <h2 className="text-lg font-medium text-gray-900 mb-4">Validate Agreement</h2>
              <div className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Agreement *</label>
                  <select
                    value={validationAgreement}
                    onChange={(e) => setValidationAgreement(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  >
                    <option value="">Select agreement…</option>
                    {agreements.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.title} ({a.status})
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  onClick={handleValidate}
                  disabled={!validationAgreement}
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
                >
                  Run validation
                </button>
              </div>
              <p className="mt-3 text-xs text-gray-400">
                Evaluates the agreement against active rules for its governing law, reporting
                blocking failures with source citations.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}