"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listAgreements,
  listVersions,
  createSignatureRequest,
  listSignatureRequests,
  SignatureRequest,
  sendSignatureRequest,
  declineSignatureRequest,
  signSignatureRequest,
  listExecutionRequirements,
  ExecutionRequirement,
  createExecutionRequirement,
  satisfyExecutionRequirement,
  checkExecutionRequirements,
  sealExecutionPackage,
  getExecutionPackage,
  ExecutionPackage,
  verifyExecutionPackage,
} from "@/lib/api";

interface Agreement {
  id: string;
  title: string;
  status: string;
  created_at: string;
}

interface Version {
  id: string;
  version_number: number;
  status: string;
  content_hash: string;
  created_at: string;
}

export default function ExecutionPage() {
  const { token } = useAuth();
  const [agreements, setAgreements] = useState<Agreement[]>([]);
  const [agreementId, setAgreementId] = useState<string>("");
  const [versions, setVersions] = useState<Version[]>([]);
  const [tab, setTab] = useState<"requests" | "requirements" | "package">("requests");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Requests
  const [requests, setRequests] = useState<SignatureRequest[]>([]);
  const [reqName, setReqName] = useState("");
  const [reqEmail, setReqEmail] = useState("");
  const [reqVersion, setReqVersion] = useState("");
  const [consentText, setConsentText] = useState(
    "I agree to sign this agreement electronically. I consent to do business electronically."
  );

  // Requirements
  const [requirements, setRequirements] = useState<ExecutionRequirement[]>([]);
  const [readiness, setReadiness] = useState<{
    ready: boolean;
    required_count: number;
    satisfied_required_count: number;
    pending_count: number;
  } | null>(null);
  const [reqType, setReqType] = useState("identity_verification");
  const [reqDescription, setReqDescription] = useState("");

  // Package
  const [packageData, setPackageData] = useState<ExecutionPackage | null>(null);
  const [sealVersion, setSealVersion] = useState("");
  const [sealHash, setSealHash] = useState("");
  const [packageVerify, setPackageVerify] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (token) {
      listAgreements(token).then(setAgreements).catch(console.error);
    }
  }, [token]);

  useEffect(() => {
    if (!token || !agreementId) return;
    listVersions(token, agreementId)
      .then((vs) => {
        setVersions(vs);
        if (vs.length > 0 && !sealVersion) setSealVersion(vs[0].id);
        if (vs.length > 0 && !reqVersion) setReqVersion(vs[0].id);
      })
      .catch(console.error);
  }, [token, agreementId]); // eslint-disable-line react-hooks/exhaustive-deps

  const reload = useCallback(() => {
    if (!token || !agreementId) return;
    setError(null);
    Promise.all([
      listSignatureRequests(token, agreementId),
      listExecutionRequirements(token, agreementId),
      checkExecutionRequirements(token, agreementId),
      getExecutionPackage(token, agreementId).catch(() => null),
    ])
      .then(([reqs, reqs2, ready, pkg]) => {
        setRequests(reqs);
        setRequirements(reqs2);
        setReadiness(ready);
        setPackageData(pkg);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load execution data"));
  }, [token, agreementId]);

  useEffect(() => {
    if (token && agreementId) reload();
  }, [token, agreementId, reload]);

  const run = async (fn: () => Promise<unknown>, successMsg: string) => {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(successMsg);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    }
  };

  const handleCreateRequest = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !agreementId || !reqName || !reqEmail || !reqVersion) return;
    run(
      () =>
        createSignatureRequest(token, agreementId, {
          name: reqName,
          email: reqEmail,
          version_id: reqVersion,
        }),
      "Signature request created"
    ).then(() => {
      setReqName("");
      setReqEmail("");
    });
  };

  const handleSign = (r: SignatureRequest) => {
    if (!token || !agreementId) return;
    const name = window.prompt("Signer name", r.name) ?? r.name;
    const email = window.prompt("Signer email", r.email) ?? r.email;
    const consent = window.prompt("Consent text", consentText) ?? consentText;
    run(
      () =>
        signSignatureRequest(token, agreementId, r.id, {
          name,
          email,
          consent_text: consent,
          identity_verified: true,
          identity_method: "internal_confirm",
        }),
      "Signature recorded"
    );
  };

  const handleCreateRequirement = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !agreementId || !reqDescription.trim()) return;
    run(
      () =>
        createExecutionRequirement(token, agreementId, {
          requirement_type: reqType,
          description: reqDescription.trim(),
        }),
      "Execution requirement added"
    ).then(() => setReqDescription(""));
  };

  const handleSeal = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !agreementId || !sealVersion || !sealHash.trim()) return;
    run(
      () =>
        sealExecutionPackage(token, agreementId, {
          version_id: sealVersion,
          final_document_hash: sealHash.trim(),
        }),
      "Execution package sealed"
    );
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        <div className="mb-6">
          <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
            ← Dashboard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 mt-1">Execution Evidence</h1>
          <p className="text-sm text-gray-500 mt-1">
            Signature requests, execution requirements and the sealed evidence package (spec 1.15).
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

        <div className="bg-white shadow rounded-lg p-4 mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">Agreement</label>
          <select
            value={agreementId}
            onChange={(e) => setAgreementId(e.target.value)}
            className="block w-full max-w-md rounded-md border-gray-300 shadow-sm focus:border-green-500 focus:ring-green-500 text-sm"
          >
            <option value="">Select an agreement…</option>
            {agreements.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title} ({a.status})
              </option>
            ))}
          </select>
        </div>

        {readiness && (
          <div
            className={`mb-6 px-4 py-3 rounded-md border text-sm ${
              readiness.ready
                ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                : "bg-amber-50 border-amber-200 text-amber-800"
            }`}
          >
            <strong>Readiness:</strong> {readiness.satisfied_required_count}/{readiness.required_count}{" "}
            required met ({readiness.pending_count} pending) —{" "}
            {readiness.ready ? "ready to execute" : "not ready"}
          </div>
        )}

        <div className="flex gap-1 bg-gray-100 p-1 rounded-xl mb-6 w-max">
          {(["requests", "requirements", "package"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-xs font-bold rounded-lg transition-all ${
                tab === t
                  ? "bg-gradient-to-r from-green-600 to-emerald-500 text-white shadow-md"
                  : "text-gray-500 hover:text-gray-800"
              }`}
            >
              {t === "requests" ? "Signature Requests" : t === "requirements" ? "Requirements" : "Evidence Package"}
            </button>
          ))}
        </div>

        {tab === "requests" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-medium text-gray-900">
                  Signature Requests ({requests.length})
                </h2>
              </div>
              {requests.length === 0 ? (
                <p className="px-6 py-12 text-sm text-gray-500 text-center">
                  No signature requests yet.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Signer</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Role</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Sent</th>
                        <th className="px-6 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {requests.map((r) => (
                        <tr key={r.id} className="hover:bg-gray-50">
                          <td className="px-6 py-4">
                            <div className="text-sm font-bold text-gray-900">{r.name}</div>
                            <div className="text-xs text-gray-500">{r.email}</div>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-600">{r.role}</td>
                          <td className="px-6 py-4">
                            <span
                              className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border ${
                                r.status === "signed"
                                  ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                                  : r.status === "sent"
                                    ? "bg-blue-100 text-blue-800 border-blue-200"
                                    : r.status === "declined"
                                      ? "bg-rose-100 text-rose-800 border-rose-200"
                                      : "bg-gray-100 text-gray-600 border-gray-200"
                              }`}
                            >
                              {r.status}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-xs text-gray-500">
                            {r.sent_at ? new Date(r.sent_at).toLocaleString() : "—"}
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex flex-wrap justify-end gap-2">
                              {r.status === "pending" && (
                                <button
                                  onClick={() =>
                                    run(
                                      () => sendSignatureRequest(token!, agreementId, r.id),
                                      "Request sent"
                                    )
                                  }
                                  className="px-3 py-1.5 text-xs font-bold rounded-lg bg-blue-600 text-white hover:bg-blue-700"
                                >
                                  Send
                                </button>
                              )}
                              {(r.status === "pending" || r.status === "sent") && (
                                <>
                                  <button
                                    onClick={() =>
                                      run(
                                        () =>
                                          declineSignatureRequest(
                                            token!,
                                            agreementId,
                                            r.id,
                                            window.prompt("Reason") ?? undefined
                                          ),
                                        "Request declined"
                                      )
                                    }
                                    className="px-3 py-1.5 text-xs font-bold rounded-lg bg-amber-100 text-amber-800 hover:bg-amber-200"
                                  >
                                    Decline
                                  </button>
                                  <button
                                    onClick={() => handleSign(r)}
                                    className="px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700"
                                  >
                                    Sign
                                  </button>
                                </>
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
              <h2 className="text-lg font-medium text-gray-900 mb-4">New Request</h2>
              <form onSubmit={handleCreateRequest} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Name *</label>
                  <input
                    value={reqName}
                    onChange={(e) => setReqName(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Email *</label>
                  <input
                    type="email"
                    value={reqEmail}
                    onChange={(e) => setReqEmail(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Version *</label>
                  <select
                    value={reqVersion}
                    onChange={(e) => setReqVersion(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  >
                    <option value="">Select version…</option>
                    {versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number} ({v.status})
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  type="submit"
                  disabled={!agreementId}
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700 disabled:opacity-50"
                >
                  Create request
                </button>
              </form>
              <p className="mt-3 text-xs text-gray-400">
                Consent text used when signing: {consentText.slice(0, 60)}…
              </p>
            </div>
          </div>
        )}

        {tab === "requirements" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-medium text-gray-900">
                  Execution Requirements ({requirements.length})
                </h2>
              </div>
              {requirements.length === 0 ? (
                <p className="px-6 py-12 text-sm text-gray-500 text-center">
                  No execution requirements yet.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Type</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Description</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Severity</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                        <th className="px-6 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {requirements.map((r) => (
                        <tr key={r.id} className="hover:bg-gray-50">
                          <td className="px-6 py-4 text-sm font-medium text-gray-900">{r.requirement_type}</td>
                          <td className="px-6 py-4 text-sm text-gray-600">{r.description}</td>
                          <td className="px-6 py-4">
                            <span className={`px-2 py-0.5 text-[10px] uppercase font-bold rounded-full ${r.severity === "required" ? "bg-rose-100 text-rose-700" : "bg-gray-100 text-gray-600"}`}>
                              {r.severity}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-600">{r.status}</td>
                          <td className="px-6 py-4 text-right">
                            {r.status === "pending" && (
                              <button
                                onClick={() =>
                                  run(
                                    () => satisfyExecutionRequirement(token!, agreementId, r.id),
                                    "Requirement satisfied"
                                  )
                                }
                                className="px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700"
                              >
                                Satisfy
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="bg-white shadow rounded-lg p-6 h-fit">
              <h2 className="text-lg font-medium text-gray-900 mb-4">Add Requirement</h2>
              <form onSubmit={handleCreateRequirement} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Type</label>
                  <select
                    value={reqType}
                    onChange={(e) => setReqType(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                  >
                    <option value="identity_verification">identity_verification</option>
                    <option value="board_approval">board_approval</option>
                    <option value="legal_review">legal_review</option>
                    <option value="payment_deposit">payment_deposit</option>
                    <option value="regulatory_filing">regulatory_filing</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Description *</label>
                  <textarea
                    value={reqDescription}
                    onChange={(e) => setReqDescription(e.target.value)}
                    rows={3}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <button
                  type="submit"
                  disabled={!agreementId}
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700 disabled:opacity-50"
                >
                  Add requirement
                </button>
              </form>
            </div>
          </div>
        )}

        {tab === "package" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
                <h2 className="text-lg font-medium text-gray-900">Sealed Execution Package</h2>
                <div className="flex gap-2">
                  {packageData && (
                    <button
                      onClick={() =>
                        run(
                          () => verifyExecutionPackage(token!, agreementId).then(setPackageVerify),
                          "Package verified"
                        )
                      }
                      className="px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700"
                    >
                      ✓ Verify package
                    </button>
                  )}
                </div>
              </div>
              {packageVerify && (
                <div className="px-6 py-3 border-b border-gray-200 bg-emerald-50 text-sm text-emerald-800">
                  <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(packageVerify, null, 2)}</pre>
                </div>
              )}
              {packageData ? (
                <div className="p-6 space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-xs text-gray-500 uppercase tracking-wider">Status</div>
                      <div className="text-sm font-bold text-gray-900 mt-0.5">{packageData.status}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase tracking-wider">Sealed at</div>
                      <div className="text-sm text-gray-900 mt-0.5">
                        {packageData.sealed_at ? new Date(packageData.sealed_at).toLocaleString() : "—"}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase tracking-wider">Final doc hash</div>
                      <div className="text-xs font-mono text-gray-700 mt-0.5 break-all">{packageData.final_document_hash}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase tracking-wider">Package hash</div>
                      <div className="text-xs font-mono text-gray-700 mt-0.5 break-all">{packageData.package_hash}</div>
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">
                      Package items ({packageData.items.length})
                    </div>
                    <ul className="divide-y divide-gray-200 border border-gray-200 rounded-lg">
                      {packageData.items.map((item) => (
                        <li key={item.id} className="px-4 py-3">
                          <div className="text-sm font-medium text-gray-900">{item.evidence_type}</div>
                          <div className="text-xs font-mono text-gray-500 break-all">{item.content_hash}</div>
                        </li>
                      ))}
                      {packageData.items.length === 0 && (
                        <li className="px-4 py-3 text-sm text-gray-500">No items yet.</li>
                      )}
                    </ul>
                  </div>
                </div>
              ) : (
                <p className="px-6 py-12 text-sm text-gray-500 text-center">
                  No execution package sealed yet. Seal one on the right.
                </p>
              )}
            </div>

            <div className="bg-white shadow rounded-lg p-6 h-fit">
              <h2 className="text-lg font-medium text-gray-900 mb-4">Seal Package</h2>
              <form onSubmit={handleSeal} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Version *</label>
                  <select
                    value={sealVersion}
                    onChange={(e) => setSealVersion(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
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
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Final document hash *
                  </label>
                  <input
                    value={sealHash}
                    onChange={(e) => setSealHash(e.target.value)}
                    placeholder="sha256:…"
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm font-mono"
                    required
                  />
                </div>
                <button
                  type="submit"
                  disabled={!agreementId}
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700 disabled:opacity-50"
                >
                  Seal package
                </button>
              </form>
              <p className="mt-3 text-xs text-gray-400">
                Sealing captures signature records, hashes and evidence items into an immutable,
                verifiable package.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}