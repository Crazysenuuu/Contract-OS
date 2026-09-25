"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { usePermissions } from "@/hooks/usePermissions";
import QualityCheckPanel from "@/components/QualityCheckPanel";
import AgreementLifecyclePanel from "@/components/AgreementLifecyclePanel";
import AgreementMonitoringPanel from "@/components/monitoring/AgreementMonitoringPanel";
import {
  getAgreement,
  getWorkflowState,
  getWorkflowActions,
  transitionWorkflow,
  validateAgreement,
  renderAgreement,
  listExternalParties,
  addExternalParty,
  listParticipants,
  addParticipant,
  listParties,
  grantPermission,
  listVersions,
} from "@/lib/api";

interface Agreement {
  id: string;
  title: string;
  status: string;
  data: Record<string, unknown>;
  governing_law?: string | null;
  effective_date?: string | null;
}

interface WorkflowState {
  key: string;
  name: string;
}

interface WorkflowAction {
  action_key: string;
  name: string;
  description: string;
}

interface ExternalParty {
  id: string;
  company_name: string;
  signatory_name: string;
  signatory_email: string;
  status: string;
  access_token: string;
}

interface Participant {
  id: string;
  user_id: string;
  participant_role: string;
  status: string;
}

interface AgreementParty {
  id: string;
  legal_entity_id: string;
  party_role: string;
  display_name: string | null;
}

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  internal_review: "bg-blue-100 text-blue-800",
  pending_approval: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  sent: "bg-purple-100 text-purple-800",
  viewed: "bg-indigo-100 text-indigo-800",
  negotiation: "bg-yellow-100 text-yellow-800",
  negotiating: "bg-yellow-100 text-yellow-800",
  ready_for_signature: "bg-orange-100 text-orange-800",
  signing: "bg-orange-100 text-orange-800",
  partially_signed: "bg-amber-100 text-amber-800",
  executed: "bg-green-100 text-green-800",
  active: "bg-emerald-100 text-emerald-800",
  expiring: "bg-amber-100 text-amber-800",
  renewed: "bg-teal-100 text-teal-800",
  expired: "bg-gray-200 text-gray-700",
  terminated: "bg-red-100 text-red-800",
  superseded: "bg-slate-100 text-slate-700",
  cancelled: "bg-red-100 text-red-800",
};

const permissionLabels: Record<string, string> = {
  "agreement.view": "View",
  "agreement.comment": "Comment",
  "agreement.propose_change": "Propose Changes",
  "agreement.approve": "Approve",
  "agreement.sign": "Sign",
  "agreement.manage_participants": "Manage Participants",
};

export default function AgreementDetailPage() {
  const { id } = useParams();
  const { token } = useAuth();
  const {
    permissions: myPermissions,
    canView,
    canManageParticipants,
  } = usePermissions(id as string);

  const [agreement, setAgreement] = useState<Agreement | null>(null);
  const [workflowState, setWorkflowState] = useState<WorkflowState | null>(
    null
  );
  const [actions, setActions] = useState<WorkflowAction[]>([]);
  const [externalParties, setExternalParties] = useState<ExternalParty[]>([]);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [parties, setParties] = useState<AgreementParty[]>([]);
  const [versions, setVersions] = useState<{ id: string; version_number: number; status: string; created_at: string; content_hash: string | null }[]>([]);
  const [loading, setLoading] = useState(true);
  const [transitioning, setTransitioning] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState("");

  // External party form
  const [showExternalForm, setShowExternalForm] = useState(false);
  const [externalForm, setExternalForm] = useState({
    company_name: "",
    signatory_name: "",
    signatory_email: "",
    signatory_title: "",
  });
  const [addingExternal, setAddingExternal] = useState(false);

  // Permission grant form
  const [showPermissionForm, setShowPermissionForm] = useState(false);
  const [selectedParticipant, setSelectedParticipant] = useState<string>("");
  const [selectedPermission, setSelectedPermission] = useState<string>("");
  const [showAddParticipant, setShowAddParticipant] = useState(false);
  const [newParticipant, setNewParticipant] = useState<{ userId: string; role: string }>({
    userId: "",
    role: "reviewer",
  });

  useEffect(() => {
    if (token && id) {
      Promise.all([
        getAgreement(token, id as string),
        getWorkflowState(token, id as string).catch(() => null),
        getWorkflowActions(token, id as string).catch(() => []),
        listExternalParties(token, id as string).catch(() => []),
        listParticipants(token, id as string).catch(() => []),
        listParties(token, id as string).catch(() => []),
        listVersions(token, id as string).catch(() => []),
      ])
        .then(([agr, state, acts, ext, parts, prts, vers]) => {
          setAgreement(agr);
          setWorkflowState(state);
          setActions(acts);
          setExternalParties(ext);
          setParticipants(parts);
          setParties(prts);
          setVersions(vers);
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [token, id]);

  const handleTransition = async (actionKey: string) => {
    if (!token || !id) return;

    try {
      setTransitioning(true);
      setError("");
      await transitionWorkflow(token, id as string, actionKey);

      const [state, acts] = await Promise.all([
        getWorkflowState(token, id as string),
        getWorkflowActions(token, id as string),
      ]);
      setWorkflowState(state);
      setActions(acts);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Transition failed");
    } finally {
      setTransitioning(false);
    }
  };

  const handleRender = async () => {
    if (!token || !id) return;

    try {
      setRendering(true);
      setError("");

      const validation = await validateAgreement(token, id as string);
      if (!validation.valid) {
        setError(`Missing fields: ${validation.errors.join(", ")}`);
        return;
      }

      const response = await renderAgreement(token, id as string, true);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `agreement-${id}.pdf`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Render failed");
    } finally {
      setRendering(false);
    }
  };

  const handleAddExternal = async () => {
    if (!token || !id) return;

    setAddingExternal(true);
    try {
      const party = await fetch(`/api/v1/agreements/${id}/parties`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          legal_entity_id: "00000000-0000-0000-0000-000000000000",
          party_role: "receiving",
          display_name: externalForm.company_name,
        }),
      }).then((r) => r.json());

      const ext = await addExternalParty(token, id as string, {
        agreement_party_id: party.id,
        ...externalForm,
      });

      setExternalParties([
        ...externalParties,
        {
          id: ext.id,
          company_name: externalForm.company_name,
          signatory_name: externalForm.signatory_name,
          signatory_email: externalForm.signatory_email,
          status: ext.status,
          access_token: ext.access_token,
        },
      ]);
      setShowExternalForm(false);
      setExternalForm({
        company_name: "",
        signatory_name: "",
        signatory_email: "",
        signatory_title: "",
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add party");
    } finally {
      setAddingExternal(false);
    }
  };

  const handleGrantPermission = async () => {
    if (!token || !id || !selectedParticipant || !selectedPermission) return;

    try {
      await grantPermission(token, id as string, {
        participant_id: selectedParticipant,
        permission_key: selectedPermission,
        granted: true,
      });
      setShowPermissionForm(false);
      setSelectedParticipant("");
      setSelectedPermission("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to grant permission");
    }
  };

  const handleAddParticipant = async () => {
    if (!token || !id || !newParticipant.userId) return;
    try {
      await addParticipant(token, id as string, {
        user_id: newParticipant.userId,
        agreement_party_id: parties[0]?.id ?? "",
        participant_role: newParticipant.role,
        can_view: true,
      });
      setParticipants(await listParticipants(token, id as string));
      setNewParticipant({ userId: "", role: "reviewer" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add participant");
    }
  };

  const copyReviewLink = (accessToken: string) => {
    const url = `${window.location.origin}/review/${accessToken}`;
    navigator.clipboard.writeText(url);
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading...</div>;
  }

  if (!agreement) {
    return (
      <div className="text-center py-12 text-gray-500">
        Agreement not found
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          href="/dashboard"
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          ← Back to Dashboard
        </Link>
      </div>

      <div className="flex justify-between items-start mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            {agreement.title}
          </h1>
          <div className="flex items-center space-x-3 mt-2">
            <span
              className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                statusColors[agreement.status] || "bg-gray-100"
              }`}
            >
              {agreement.status}
            </span>
            {workflowState && (
              <span className="text-sm text-gray-500">
                Workflow: {workflowState.name}
              </span>
            )}
          </div>
        </div>

        <div className="flex space-x-3">
          {canView && (
            <Link
              href={`/agreements/negotiate?id=${id}`}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
            >
              Negotiate
            </Link>
          )}
          <Link
            href={`/agreements/analyze?id=${id}`}
            className="px-4 py-2 text-sm font-medium text-purple-700 bg-purple-50 border border-purple-300 rounded-md hover:bg-purple-100"
          >
            AI Analysis
          </Link>
          <Link
            href={`/agreements/obligations?id=${id}`}
            className="px-4 py-2 text-sm font-medium text-orange-700 bg-orange-50 border border-orange-300 rounded-md hover:bg-orange-100"
          >
            Obligations
          </Link>
          <Link
            href={`/agreements/compliance?id=${id}`}
            className="px-4 py-2 text-sm font-medium text-emerald-700 bg-emerald-50 border border-emerald-300 rounded-md hover:bg-emerald-100"
          >
            Compliance
          </Link>
          <Link
            href={`/clause-library?agreement=${id}`}
            className="px-4 py-2 text-sm font-medium text-teal-700 bg-teal-50 border border-teal-300 rounded-md hover:bg-teal-100"
          >
            Clauses
          </Link>
          <Link
            href={`/audit?agreement=${id}`}
            className="px-4 py-2 text-sm font-medium text-indigo-700 bg-indigo-50 border border-indigo-300 rounded-md hover:bg-indigo-100"
          >
            Audit
          </Link>
          <Link
            href={`/documents?agreement=${id}`}
            className="px-4 py-2 text-sm font-medium text-cyan-700 bg-cyan-50 border border-cyan-300 rounded-md hover:bg-cyan-100"
          >
            Documents
          </Link>
          <button
            onClick={handleRender}
            disabled={rendering}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {rendering ? "Generating..." : "Download PDF"}
          </button>
        </div>
      </div>

      {/* Permissions Badge */}
      <div className="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-lg">
        <h3 className="text-sm font-medium text-blue-800 mb-2">
          Your Permissions
        </h3>
        <div className="flex flex-wrap gap-2">
          {myPermissions.map((perm) => (
            <span
              key={perm}
              className="px-2 py-1 text-xs bg-blue-100 text-blue-800 rounded"
            >
              {permissionLabels[perm] || perm}
            </span>
          ))}
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Agreement Details */}
        <div className="lg:col-span-2 space-y-6">
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">
              Agreement Details
            </h2>

            <dl className="grid grid-cols-2 gap-4">
              <div>
                <dt className="text-sm text-gray-500">Governing Law</dt>
                <dd className="text-sm text-gray-900">
                  {agreement.governing_law || "Not specified"}
                </dd>
              </div>
              <div>
                <dt className="text-sm text-gray-500">Effective Date</dt>
                <dd className="text-sm text-gray-900">
                  {agreement.effective_date || "Not specified"}
                </dd>
              </div>
            </dl>

            {Object.keys(agreement.data).length > 0 && (
              <div className="mt-6">
                <h3 className="text-sm font-medium text-gray-700 mb-2">
                  Agreement Data
                </h3>
                <div className="bg-gray-50 rounded p-4 text-sm">
                  {Object.entries(agreement.data).map(([key, value]) => (
                    <div key={key} className="flex justify-between py-1">
                      <span className="text-gray-500">{key}:</span>
                      <span className="text-gray-900">
                        {typeof value === "boolean"
                          ? value
                            ? "Yes"
                            : "No"
                          : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Versions history (spec §26 / §48) */}
          <div className="bg-white shadow rounded-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium text-gray-900">Versions</h2>
              <Link
                href={`/agreements/${id}/versions`}
                className="text-sm text-blue-600 hover:text-blue-800"
              >
                Manage versions →
              </Link>
            </div>
            {versions.length === 0 ? (
              <p className="text-sm text-gray-500">No versions yet</p>
            ) : (
              <div className="space-y-2">
                {versions.map((v) => (
                  <div
                    key={v.id}
                    className="flex items-center justify-between p-3 border border-gray-200 rounded-lg"
                  >
                    <div>
                      <div className="font-medium text-sm text-gray-900">
                        Version {v.version_number}
                      </div>
                      <div className="text-xs text-gray-500">
                        {new Date(v.created_at).toLocaleString()} · {v.status}
                        {v.content_hash && (
                          <span className="ml-2 font-mono text-gray-400">
                            {v.content_hash.slice(0, 8)}
                          </span>
                        )}
                      </div>
                    </div>
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full ${
                        v.status === "locked"
                          ? "bg-green-100 text-green-800"
                          : v.status === "current"
                          ? "bg-blue-100 text-blue-800"
                          : "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {v.status}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Contract quality findings (spec 77-81) */}
          <QualityCheckPanel agreementId={id as string} />

          {/* Signature progress, amendments, terminations (spec §36, §66, §67) */}
          {token && agreement && (
            <AgreementLifecyclePanel
              token={token}
              agreementId={id as string}
              status={agreement.status}
              onChanged={() =>
                getAgreement(token, id as string)
                  .then(setAgreement)
                  .catch(console.error)
              }
            />
          )}

          {/* Parties (named contracting entities) */}
          {parties.length > 0 && (
            <div className="bg-white shadow rounded-lg p-6">
              <h2 className="text-lg font-medium text-gray-900 mb-4">Parties</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {parties.map((party) => (
                  <div
                    key={party.id}
                    className="flex items-center justify-between p-3 border border-gray-200 rounded-lg"
                  >
                    <div>
                      <div className="font-medium text-sm text-gray-900">
                        {party.display_name ?? party.legal_entity_id.slice(0, 8) + "…"}
                      </div>
                      <div className="text-xs text-gray-500 capitalize">{party.party_role.replace(/_/g, " ")}</div>
                    </div>
                    <span className="text-xs text-gray-400 font-mono">
                      {party.legal_entity_id.slice(0, 8)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Participants */}
          <div className="bg-white shadow rounded-lg p-6">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-medium text-gray-900">
                Participants
              </h2>
              {canManageParticipants && (
                <div className="flex gap-4">
                  <button
                    onClick={() => setShowAddParticipant(!showAddParticipant)}
                    className="text-sm text-blue-600 hover:text-blue-800"
                  >
                    + Add Participant
                  </button>
                  <button
                    onClick={() => setShowPermissionForm(!showPermissionForm)}
                    className="text-sm text-blue-600 hover:text-blue-800"
                  >
                    + Grant Permission
                  </button>
                </div>
              )}
            </div>

            {participants.length === 0 ? (
              <p className="text-sm text-gray-500">
                No participants added yet
              </p>
            ) : (
              <div className="space-y-3">
                {participants.map((participant) => (
                  <div
                    key={participant.id}
                    className="flex items-center justify-between p-3 border border-gray-200 rounded-lg"
                  >
                    <div>
                      <div className="font-medium text-sm text-gray-900">
                        {participant.participant_role}
                      </div>
                      <div className="text-xs text-gray-500">
                        {participant.user_id.slice(0, 8)}...
                      </div>
                    </div>
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full ${
                        participant.status === "active"
                          ? "bg-green-100 text-green-800"
                          : "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {participant.status}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Add Participant Form */}
            {showAddParticipant && canManageParticipants && (
              <div className="mt-4 p-4 bg-gray-50 rounded-lg">
                <h3 className="text-sm font-medium text-gray-700 mb-3">Add Participant</h3>
                <div className="space-y-3">
                  <input
                    type="text"
                    placeholder="User ID (uuid)"
                    value={newParticipant.userId}
                    onChange={(e) => setNewParticipant({ ...newParticipant, userId: e.target.value })}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                  <select
                    value={newParticipant.role}
                    onChange={(e) => setNewParticipant({ ...newParticipant, role: e.target.value })}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  >
                    <option value="viewer">Viewer</option>
                    <option value="reviewer">Reviewer</option>
                    <option value="approver">Approver</option>
                    <option value="signer">Signer</option>
                  </select>
                  <div className="flex space-x-3">
                    <button
                      onClick={handleAddParticipant}
                      disabled={!newParticipant.userId || parties.length === 0}
                      title={parties.length === 0 ? "Agreement has no parties yet" : undefined}
                      className="px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700 disabled:opacity-50"
                    >
                      Add
                    </button>
                    <button
                      onClick={() => setShowAddParticipant(false)}
                      className="px-4 py-2 border border-gray-300 text-sm rounded-md hover:bg-gray-50"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Grant Permission Form */}
            {showPermissionForm && canManageParticipants && (
              <div className="mt-4 p-4 bg-gray-50 rounded-lg">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                  Grant Permission
                </h3>
                <div className="space-y-3">
                  <select
                    value={selectedParticipant}
                    onChange={(e) => setSelectedParticipant(e.target.value)}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  >
                    <option value="">Select participant</option>
                    {participants.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.participant_role} ({p.user_id.slice(0, 8)}...)
                      </option>
                    ))}
                  </select>
                  <select
                    value={selectedPermission}
                    onChange={(e) => setSelectedPermission(e.target.value)}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  >
                    <option value="">Select permission</option>
                    {Object.entries(permissionLabels).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <div className="flex space-x-3">
                    <button
                      onClick={handleGrantPermission}
                      disabled={!selectedParticipant || !selectedPermission}
                      className="px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700 disabled:opacity-50"
                    >
                      Grant
                    </button>
                    <button
                      onClick={() => setShowPermissionForm(false)}
                      className="px-4 py-2 border border-gray-300 text-sm rounded-md hover:bg-gray-50"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
)}
          </div>

        {/* Monitoring (spec 3.15.39) */}
        {token && (
          <div className="mt-6">
            <AgreementMonitoringPanel agreementId={id as string} token={token} />
          </div>
        )}

        {/* External Parties */}
        <div className="bg-white shadow rounded-lg p-6">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-medium text-gray-900">
                External Parties
              </h2>
              {canManageParticipants && (
                <button
                  onClick={() => setShowExternalForm(!showExternalForm)}
                  className="text-sm text-blue-600 hover:text-blue-800"
                >
                  + Add Party
                </button>
              )}
            </div>

            {externalParties.length === 0 ? (
              <p className="text-sm text-gray-500">
                No external parties added yet
              </p>
            ) : (
              <div className="space-y-3">
                {externalParties.map((party) => (
                  <div
                    key={party.id}
                    className="flex items-center justify-between p-3 border border-gray-200 rounded-lg"
                  >
                    <div>
                      <div className="font-medium text-sm text-gray-900">
                        {party.company_name}
                      </div>
                      <div className="text-xs text-gray-500">
                        {party.signatory_name} ({party.signatory_email})
                      </div>
                    </div>
                    <div className="flex items-center space-x-2">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${
                          party.status === "signed"
                            ? "bg-green-100 text-green-800"
                            : party.status === "accepted"
                            ? "bg-blue-100 text-blue-800"
                            : "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {party.status}
                      </span>
                      <button
                        onClick={() => copyReviewLink(party.access_token)}
                        className="text-xs text-gray-500 hover:text-gray-700"
                        title="Copy review link"
                      >
                        📋
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Add External Party Form */}
            {showExternalForm && (
              <div className="mt-4 p-4 bg-gray-50 rounded-lg">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                  Add External Party
                </h3>
                <div className="space-y-3">
                  <input
                    type="text"
                    placeholder="Company Name"
                    value={externalForm.company_name}
                    onChange={(e) =>
                      setExternalForm({
                        ...externalForm,
                        company_name: e.target.value,
                      })
                    }
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                  <input
                    type="text"
                    placeholder="Signatory Name"
                    value={externalForm.signatory_name}
                    onChange={(e) =>
                      setExternalForm({
                        ...externalForm,
                        signatory_name: e.target.value,
                      })
                    }
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                  <input
                    type="email"
                    placeholder="Signatory Email"
                    value={externalForm.signatory_email}
                    onChange={(e) =>
                      setExternalForm({
                        ...externalForm,
                        signatory_email: e.target.value,
                      })
                    }
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                  <input
                    type="text"
                    placeholder="Title (optional)"
                    value={externalForm.signatory_title}
                    onChange={(e) =>
                      setExternalForm({
                        ...externalForm,
                        signatory_title: e.target.value,
                      })
                    }
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                  <div className="flex space-x-3">
                    <button
                      onClick={handleAddExternal}
                      disabled={
                        addingExternal ||
                        !externalForm.company_name ||
                        !externalForm.signatory_name ||
                        !externalForm.signatory_email
                      }
                      className="px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700 disabled:opacity-50"
                    >
                      {addingExternal ? "Adding..." : "Add Party"}
                    </button>
                    <button
                      onClick={() => setShowExternalForm(false)}
                      className="px-4 py-2 border border-gray-300 text-sm rounded-md hover:bg-gray-50"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Workflow Actions */}
        <div>
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">
              Actions
            </h2>

            {actions.length === 0 ? (
              <p className="text-sm text-gray-500">
                No actions available for current state
              </p>
            ) : (
              <div className="space-y-3">
                {actions.map((action) => (
                  <button
                    key={action.action_key}
                    onClick={() => handleTransition(action.action_key)}
                    disabled={transitioning}
                    className="w-full text-left px-4 py-3 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
                  >
                    <div className="font-medium text-sm text-gray-900">
                      {action.name}
                    </div>
                    {action.description && (
                      <div className="text-xs text-gray-500 mt-1">
                        {action.description}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
