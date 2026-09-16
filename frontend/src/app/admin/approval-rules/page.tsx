"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  listRuleDefinitions,
  createRuleDefinition,
  deleteRuleDefinition,
  evaluateRules,
  type RuleCondition,
  type RuleDefinition,
} from "@/lib/api";

// ─── Rule vocabulary (must match backend rules_engine operators) ────────────

const FIELDS: Array<{ key: string; label: string; kind: "number" | "text" }> = [
  { key: "agreement_value", label: "Agreement value", kind: "number" },
  { key: "agreement_type", label: "Agreement type", kind: "text" },
  { key: "currency", label: "Currency", kind: "text" },
  { key: "risk_score", label: "Risk score", kind: "number" },
  { key: "counterparty_country", label: "Counterparty country", kind: "text" },
];

const OPERATORS: Record<"number" | "text", Array<{ key: string; label: string }>> = {
  number: [
    { key: "greater_than", label: "is greater than" },
    { key: "greater_than_or_equal", label: "is at least" },
    { key: "less_than", label: "is less than" },
    { key: "less_than_or_equal", label: "is at most" },
    { key: "equal_to", label: "equals" },
    { key: "not_equal_to", label: "does not equal" },
  ],
  text: [
    { key: "equal_to", label: "equals" },
    { key: "not_equal_to", label: "does not equal" },
    { key: "contains", label: "contains" },
    { key: "not_contains", label: "does not contain" },
    { key: "starts_with", label: "starts with" },
    { key: "is_in", label: "is one of (comma-separated)" },
  ],
};

interface DraftCondition {
  field: string;
  operator: string;
  value: string;
}

const emptyCondition = (): DraftCondition => ({
  field: "agreement_value",
  operator: "greater_than",
  value: "",
});

const toPayloadCondition = (c: DraftCondition): RuleCondition => {
  const field = FIELDS.find((f) => f.key === c.field);
  let value: unknown = c.value;
  if (field?.kind === "number") {
    value = Number(c.value);
  } else if (c.operator === "is_in") {
    value = c.value.split(",").map((s) => s.trim()).filter(Boolean);
  }
  return { name: c.field, operator: c.operator, value };
};

// ─── Page ────────────────────────────────────────────────────────────────────

export default function ApprovalRulesPage() {
  const { user, token, isLoading } = useAuth();
  const router = useRouter();

  const [rules, setRules] = useState<RuleDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Builder state
  const [name, setName] = useState("");
  const [conditions, setConditions] = useState<DraftCondition[]>([emptyCondition()]);
  const [approvalRole, setApprovalRole] = useState("cfo");
  const [saving, setSaving] = useState(false);

  // Dry-run evaluator state
  const [evalValue, setEvalValue] = useState("");
  const [evalType, setEvalType] = useState("");
  const [evalResult, setEvalResult] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoading && !token) router.replace("/login");
    if (!isLoading && token && user && !user.is_admin) router.replace("/dashboard");
  }, [isLoading, token, user, router]);

  // No leading setLoading(true): on mount `loading` already starts as true,
  // avoiding a synchronous setState cascade from the mount effect.
  const loadRules = useCallback(async () => {
    if (!token) return;
    try {
      setRules(await listRuleDefinitions(token));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load rules");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    // Defer so the effect body never triggers a synchronous setState cascade.
    queueMicrotask(() => loadRules());
  }, [loadRules]);

  const saveRule = async () => {
    if (!token || !name.trim()) return;
    setSaving(true);
    try {
      await createRuleDefinition(token, {
        name: name.trim(),
        rules: {
          conditions: { all: conditions.map(toPayloadCondition) },
          actions: [{ name: "require_approval", params: { role: approvalRole } }],
        },
        stages: [
          {
            name: `${approvalRole.toUpperCase()} approval`,
            order: 1,
            required_role: approvalRole,
            execution_mode: "sequential",
            require_all_approvers: true,
          },
        ],
      });
      setName("");
      setConditions([emptyCondition()]);
      await loadRules();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save rule");
    } finally {
      setSaving(false);
    }
  };

  const dryRun = async () => {
    if (!token) return;
    try {
      const res = await evaluateRules(token, {
        agreement_value: Number(evalValue || 0),
        agreement_type: evalType || undefined,
      });
      setEvalResult(
        res.total > 0
          ? `Matched "${res.name}" — requires ${res.required_approvals.map((a) => a.role).join(", ")}`
          : "No rule matched — no approval required"
      );
    } catch (e) {
      setEvalResult(e instanceof Error ? e.message : "Evaluation failed");
    }
  };

  const humanize = (r: RuleDefinition) => {
    const conds = r.rules?.conditions && "all" in r.rules.conditions ? r.rules.conditions.all : [];
    if (!conds.length) return r.description || "Custom rule";
    return conds
      .map((c) => {
        const f = FIELDS.find((x) => x.key === c.name)?.label ?? c.name;
        const op = [...OPERATORS.number, ...OPERATORS.text].find((o) => o.key === c.operator)?.label ?? c.operator;
        return `${f} ${op} ${Array.isArray(c.value) ? c.value.join(", ") : String(c.value)}`;
      })
      .join(" AND ");
  };

  if (isLoading || !token || !user?.is_admin) return null;

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Approval routing rules</h1>
      <p className="mt-1 text-sm text-slate-500">
        Define when agreements require approval (spec 24.2). Rules are evaluated in order —
        the first match routes the agreement automatically.
      </p>

      {error && (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      {/* Builder */}
      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">New rule</h2>

        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Rule name — e.g. “CFO above 100k”"
          className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
        />

        <div className="mt-4 space-y-3">
          {conditions.map((c, i) => {
            const field = FIELDS.find((f) => f.key === c.field);
            return (
              <div key={i} className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-slate-500">{i === 0 ? "IF" : "AND"}</span>
                <select
                  value={c.field}
                  onChange={(e) => {
                    const next = [...conditions];
                    next[i] = { ...c, field: e.target.value, operator: "greater_than" };
                    setConditions(next);
                  }}
                  className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                >
                  {FIELDS.map((f) => (
                    <option key={f.key} value={f.key}>{f.label}</option>
                  ))}
                </select>
                <select
                  value={c.operator}
                  onChange={(e) => {
                    const next = [...conditions];
                    next[i] = { ...c, operator: e.target.value };
                    setConditions(next);
                  }}
                  className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                >
                  {OPERATORS[field?.kind ?? "text"].map((o) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
                <input
                  value={c.value}
                  onChange={(e) => {
                    const next = [...conditions];
                    next[i] = { ...c, value: e.target.value };
                    setConditions(next);
                  }}
                  placeholder={field?.kind === "number" ? "100000" : "value"}
                  className="w-40 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                />
                {conditions.length > 1 && (
                  <button
                    onClick={() => setConditions(conditions.filter((_, j) => j !== i))}
                    className="text-sm text-rose-600 hover:underline"
                  >
                    Remove
                  </button>
                )}
              </div>
            );
          })}
        </div>

        <div className="mt-3 flex items-center gap-2 text-sm">
          <span className="text-slate-500">THEN require approval from</span>
          <input
            value={approvalRole}
            onChange={(e) => setApprovalRole(e.target.value)}
            placeholder="cfo"
            className="w-36 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
          />
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={saveRule}
            disabled={saving || !name.trim() || conditions.some((c) => !c.value)}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Create rule"}
          </button>
          <button
            onClick={() => setConditions([...conditions, emptyCondition()])}
            className="text-sm text-sky-700 hover:underline"
          >
            + Add condition
          </button>
        </div>
      </section>

      {/* Dry-run evaluator */}
      <section className="mt-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Test a scenario</h2>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            value={evalValue}
            onChange={(e) => setEvalValue(e.target.value)}
            placeholder="Agreement value"
            className="w-40 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
          />
          <input
            value={evalType}
            onChange={(e) => setEvalType(e.target.value)}
            placeholder="Agreement type (optional)"
            className="w-48 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
          />
          <button
            onClick={dryRun}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium hover:bg-slate-50"
          >
            Evaluate
          </button>
        </div>
        {evalResult && (
          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">{evalResult}</p>
        )}
      </section>

      {/* Existing rules */}
      <section className="mt-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Active rules ({rules.length})
        </h2>
        {loading ? (
          <p className="mt-3 text-sm text-slate-500">Loading…</p>
        ) : rules.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">
            No rules yet — agreements fall back to the DOA value matrix.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {rules.map((r) => (
              <li
                key={r.id}
                className="flex items-start justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
              >
                <div>
                  <p className="text-sm font-medium text-slate-900">{r.name}</p>
                  <p className="mt-0.5 text-sm text-slate-500">{humanize(r)}</p>
                </div>
                <button
                  onClick={async () => {
                    if (!token) return;
                    await deleteRuleDefinition(token, r.id);
                    await loadRules();
                  }}
                  className="ml-4 shrink-0 text-sm text-rose-600 hover:underline"
                >
                  Deactivate
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
