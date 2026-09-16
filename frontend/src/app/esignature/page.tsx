"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { createEnvelope, getEnvelopeStatus, simulateSigning } from "@/lib/api";

interface EnvelopeData {
  envelope_id: string;
  status: string;
  signing_url: string | null;
  created_at: string | null;
}

interface EnvelopeStatus {
  envelope_id: string;
  status: string;
  document_name: string;
  signers: Array<{ name: string; email: string; role: string }>;
  signatures: Array<{ signer_email: string; signed_at: string; status: string }>;
  created_at: string;
}

function ESignatureContent() {
  const searchParams = useSearchParams();
  const agreementId = searchParams.get("agreement_id") || "";
  const { token } = useAuth();

  const [signers, setSigners] = useState<Array<{ name: string; email: string; role: string }>>([
    { name: "", email: "", role: "signer" },
  ]);
  const [subject, setSubject] = useState("Please sign this agreement");
  const [message, setMessage] = useState("Please review and sign the attached agreement.");
  const [envelopeId, setEnvelopeId] = useState<string | null>(null);
  const [envelopeStatus, setEnvelopeStatus] = useState<EnvelopeStatus | null>(null);
  const [creating, setCreating] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [error, setError] = useState("");

  const addSigner = () => {
    setSigners([...signers, { name: "", email: "", role: "signer" }]);
  };

  const updateSigner = (index: number, field: string, value: string) => {
    const updated = [...signers];
    updated[index] = { ...updated[index], [field]: value };
    setSigners(updated);
  };

  const removeSigner = (index: number) => {
    setSigners(signers.filter((_, i) => i !== index));
  };

  const handleCreateEnvelope = async () => {
    if (!token || !agreementId) {
      setError("Agreement ID is required");
      return;
    }

    const validSigners = signers.filter((s) => s.name && s.email);
    if (validSigners.length === 0) {
      setError("At least one signer is required");
      return;
    }

    setCreating(true);
    setError("");
    try {
      const result = await createEnvelope(token, {
        agreement_id: agreementId,
        signers: validSigners,
        subject,
        message,
      });
      setEnvelopeId(result.envelope_id);

      // Get status
      const status = await getEnvelopeStatus(token, result.envelope_id);
      setEnvelopeStatus(status as unknown as EnvelopeStatus);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create envelope");
    } finally {
      setCreating(false);
    }
  };

  const handleSimulateSigning = async (email: string) => {
    if (!token || !envelopeId) return;

    setSimulating(true);
    try {
      await simulateSigning(token, envelopeId, email);

      // Refresh status
      const status = await getEnvelopeStatus(token, envelopeId);
      setEnvelopeStatus(status as unknown as EnvelopeStatus);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Simulation failed");
    } finally {
      setSimulating(false);
    }
  };

  const handleRefreshStatus = async () => {
    if (!token || !envelopeId) return;

    try {
      const status = await getEnvelopeStatus(token, envelopeId);
      setEnvelopeStatus(status as unknown as EnvelopeStatus);
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
          ← Back to Dashboard
        </Link>
      </div>

      <h1 className="text-2xl font-bold text-gray-900 mb-6">E-Signature</h1>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Create Envelope */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">Create Signing Envelope</h2>

          {!agreementId && (
            <div className="bg-yellow-50 border border-yellow-200 text-yellow-800 px-4 py-3 rounded mb-4 text-sm">
              No agreement ID provided. Navigate from an agreement detail page.
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-700 mb-1">Subject</label>
              <input
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">Message</label>
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                rows={3}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-medium text-gray-700">Signers</label>
                <button
                  onClick={addSigner}
                  className="text-sm text-blue-600 hover:text-blue-800"
                >
                  + Add Signer
                </button>
              </div>
              <div className="space-y-3">
                {signers.map((signer, i) => (
                  <div key={i} className="flex items-center space-x-2">
                    <input
                      type="text"
                      placeholder="Name"
                      value={signer.name}
                      onChange={(e) => updateSigner(i, "name", e.target.value)}
                      className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm"
                    />
                    <input
                      type="email"
                      placeholder="Email"
                      value={signer.email}
                      onChange={(e) => updateSigner(i, "email", e.target.value)}
                      className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm"
                    />
                    <select
                      value={signer.role}
                      onChange={(e) => updateSigner(i, "role", e.target.value)}
                      className="border border-gray-300 rounded-md px-3 py-2 text-sm"
                    >
                      <option value="signer">Signer</option>
                      <option value="approver">Approver</option>
                      <option value="cc">CC</option>
                    </select>
                    {signers.length > 1 && (
                      <button
                        onClick={() => removeSigner(i)}
                        className="text-red-500 hover:text-red-700 text-sm"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <button
              onClick={handleCreateEnvelope}
              disabled={creating || !agreementId}
              className="w-full px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? "Creating..." : "Create Envelope & Send"}
            </button>
          </div>
        </div>

        {/* Envelope Status */}
        <div className="bg-white shadow rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-medium text-gray-900">Envelope Status</h2>
            {envelopeId && (
              <button
                onClick={handleRefreshStatus}
                className="text-sm text-blue-600 hover:text-blue-800"
              >
                ↻ Refresh
              </button>
            )}
          </div>

          {!envelopeId ? (
            <p className="text-sm text-gray-500">
              Create an envelope to see its status here.
            </p>
          ) : !envelopeStatus ? (
            <p className="text-sm text-gray-500">Loading status...</p>
          ) : (
            <div className="space-y-4">
              <div>
                <div className="text-xs text-gray-500 uppercase">Envelope ID</div>
                <div className="text-sm font-mono text-gray-900">{envelopeStatus.envelope_id}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500 uppercase">Status</div>
                <span
                  className={`px-2 py-1 text-xs font-medium rounded-full ${
                    envelopeStatus.status === "completed"
                      ? "bg-green-100 text-green-800"
                      : envelopeStatus.status === "sent"
                      ? "bg-blue-100 text-blue-800"
                      : "bg-gray-100 text-gray-800"
                  }`}
                >
                  {envelopeStatus.status}
                </span>
              </div>
              <div>
                <div className="text-xs text-gray-500 uppercase">Signers</div>
                <div className="space-y-2 mt-1">
                  {envelopeStatus.signers?.map((s, i) => {
                    const signed = envelopeStatus.signatures?.find(
                      (sig) => sig.signer_email === s.email
                    );
                    return (
                      <div key={i} className="flex items-center justify-between p-2 bg-gray-50 rounded">
                        <div>
                          <div className="text-sm font-medium">{s.name}</div>
                          <div className="text-xs text-gray-500">{s.email}</div>
                        </div>
                        <div className="flex items-center space-x-2">
                          {signed ? (
                            <span className="text-xs text-green-600">✓ Signed</span>
                          ) : (
                            <button
                              onClick={() => handleSimulateSigning(s.email)}
                              disabled={simulating}
                              className="text-xs text-blue-600 hover:text-blue-800 disabled:opacity-50"
                            >
                              {simulating ? "Signing..." : "Simulate Sign"}
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ESignaturePage() {
  return (
    <Suspense fallback={<div className="text-center py-12 text-gray-500">Loading...</div>}>
      <ESignatureContent />
    </Suspense>
  );
}
