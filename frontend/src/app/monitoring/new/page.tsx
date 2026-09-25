"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  MonitoringIntegrationRow,
  createMonitoringRule,
  listAgreements,
  listMonitoringIntegrations,
  listObligations,
  listVersions,
} from "@/lib/api";

interface AgreementOption {
  id: string;
  title: string;
}

interface ObligationOption {
  id: string;
  description: string;
  status: string;
}

interface VersionOption {
  id: string;
  version_number: number;
  status: string;
}

const inputClass =
  "w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none";

export default function NewMonitoringRulePage() {
  const { token } = useAuth();
  const router = useRouter();

  const [agreements, setAgreements] = useState<AgreementOption[]>([]);
  const [obligations, setObligations] = useState<ObligationOption[]>([]);
  const [versions, setVersions] = useState<VersionOption[]>([]);
  const [integrations, setIntegrations] = useState<MonitoringIntegrationRow[]>([]);

  const [agreementId, setAgreementId] = useState("");
  const [obligationId, setObligationId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [integrationId, setIntegrationId] = useState("");
  const [resource, setResource] = useState("");
  const [evaluationKind, setEvaluationKind] = useState("existence");
  const [expected, setExpected] = useState(true);
  const [scheduleMinutes, setScheduleMinutes] = useState(60);
  const [onPass, setOnPass] = useState("NO_ACTION");
  const [status, setStatus] = useState("ACTIVE");

  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (!token) return;
    Promise.all([listAgreements(token), listMonitoringIntegrations(token)])
      .then(([agr, integ]) => {
        setAgreements(
          agr.map((a) => ({ id: a.id, title: a.title }))
        );
        setIntegrations(integ);
        setLoading(false);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load setup options");
        setLoading(false);
      });
  }, [token]);

  const onAgreementChange = (value: string) => {
    setAgreementId(value);
    setObligationId("");
    setVersionId("");
    if (!token) return;
    if (value) {
      listObligations(token, value)
        .then((rows) =>
          setObligations(
            rows.map((o) => ({
              id: o.id,
              description: o.description || "Untitled obligation",
              status: o.status,
            }))
          )
        )
        .catch(() => setObligations([]));
      listVersions(token, value)
        .then((rows) =>
          setVersions(
            rows.map((v) => ({
              id: v.id,
              version_number: v.version_number,
              status: v.status,
            }))
          )
        )
        .catch(() => setVersions([]));
    } else {
      setObligations([]);
      setVersions([]);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !agreementId || !obligationId || !versionId || !integrationId) {
      setError("Select an agreement, obligation, source version, and integration.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await createMonitoringRule(token, {
        obligation_id: obligationId,
        integration_id: integrationId,
        source_version_id: versionId,
        query_definition: { resource: resource.trim() || "default" },
        evaluation_definition:
          evaluationKind === "threshold"
            ? { kind: "threshold", threshold: expected ? 1 : 0 }
            : { kind: "existence", expected },
        schedule_definition: { recurrence: "interval", minutes: scheduleMinutes },
        automation: { on_pass: onPass },
        status,
      });
      setSuccess(true);
      setTimeout(() => router.push("/monitoring"), 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create rule");
    } finally {
      setCreating(false);
    }
  };

  if (!token) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        <p className="text-sm text-gray-500">Sign in to configure monitoring.</p>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <div>
        <Link href="/monitoring" className="text-sm text-gray-500 hover:text-gray-700">
          ← Back to Monitoring
        </Link>
        <h1 className="mt-2 text-2xl font-semibold text-gray-900">
          New monitoring rule
        </h1>
        <p className="text-sm text-gray-500">
          Configure an obligation monitoring rule against a source integration
          (spec 3.15.41).
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}
      {success && (
        <div className="rounded border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-800">
          Rule created. Redirecting to Monitoring…
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : (
        <form onSubmit={submit} className="space-y-5 bg-white rounded-lg border border-gray-200 p-6 shadow-sm">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Agreement
            </label>
            <select
              className={inputClass}
              value={agreementId}
              onChange={(e) => onAgreementChange(e.target.value)}
            >
              <option value="">Select an agreement…</option>
              {agreements.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.title}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Obligation
              </label>
              <select
                className={inputClass}
                value={obligationId}
                disabled={!agreementId}
                onChange={(e) => setObligationId(e.target.value)}
              >
                <option value="">Select obligation…</option>
                {obligations.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.description} ({o.status})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Source version
              </label>
              <select
                className={inputClass}
                value={versionId}
                disabled={!agreementId}
                onChange={(e) => setVersionId(e.target.value)}
              >
                <option value="">Select version…</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_number} ({v.status})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Source integration
              </label>
              <select
                className={inputClass}
                value={integrationId}
                onChange={(e) => setIntegrationId(e.target.value)}
              >
                <option value="">Select integration…</option>
                {integrations.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name} ({i.provider_key})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Resource path
              </label>
              <input
                className={inputClass}
                value={resource}
                placeholder="claims"
                onChange={(e) => setResource(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Evaluation
              </label>
              <select
                className={inputClass}
                value={evaluationKind}
                onChange={(e) => setEvaluationKind(e.target.value)}
              >
                <option value="existence">Existence</option>
                <option value="threshold">Threshold</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Expected present
              </label>
              <select
                className={inputClass}
                value={expected ? "true" : "false"}
                onChange={(e) => setExpected(e.target.value === "true")}
              >
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Check every (minutes)
              </label>
              <input
                type="number"
                min={1}
                className={inputClass}
                value={scheduleMinutes}
                onChange={(e) => setScheduleMinutes(Number(e.target.value))}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                On pass action
              </label>
              <select
                className={inputClass}
                value={onPass}
                onChange={(e) => setOnPass(e.target.value)}
              >
                <option value="NO_ACTION">No action</option>
                <option value="MARK_TASK_READY">Mark task ready</option>
                <option value="ATTACH_EVIDENCE">Attach evidence</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Initial status
              </label>
              <select
                className={inputClass}
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="DRAFT">Draft</option>
                <option value="ACTIVE">Active</option>
              </select>
            </div>
          </div>

          <button
            type="submit"
            disabled={creating}
            className="px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {creating ? "Creating…" : "Create rule"}
          </button>
        </form>
      )}
    </div>
  );
}