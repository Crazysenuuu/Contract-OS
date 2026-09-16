"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  reviewAgreement,
  addExternalComment,
  acceptExternal,
  rejectExternal,
  signExternal,
} from "@/lib/api";

interface ReviewData {
  agreement_id: string;
  title: string;
  company_name: string;
  signatory_name: string;
  version_number: number;
  content: string;
  status: string;
}

export default function ReviewPage() {
  const { token } = useParams();
  const [data, setData] = useState<ReviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  // Comment form
  const [comment, setComment] = useState("");
  const [clauseId, setClauseId] = useState("");
  const [commentSuccess, setCommentSuccess] = useState(false);

  // Sign form
  const [showSignForm, setShowSignForm] = useState(false);
  const [consentText, setConsentText] = useState(
    "I have read and agree to the terms of this agreement."
  );

  useEffect(() => {
    if (token) {
      reviewAgreement(token as string)
        .then(setData)
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false));
    }
  }, [token]);

  const handleComment = async () => {
    if (!token || !comment.trim()) return;
    setActionLoading(true);
    try {
      await addExternalComment(token as string, {
        content: comment,
        clause_identifier: clauseId || undefined,
      });
      setComment("");
      setClauseId("");
      setCommentSuccess(true);
      setTimeout(() => setCommentSuccess(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Comment failed");
    } finally {
      setActionLoading(false);
    }
  };

  const handleAccept = async () => {
    if (!token) return;
    setActionLoading(true);
    try {
      await acceptExternal(token as string);
      setData((prev) => (prev ? { ...prev, status: "accepted" } : null));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Accept failed");
    } finally {
      setActionLoading(false);
    }
  };

  const handleReject = async () => {
    if (!token) return;
    setActionLoading(true);
    try {
      await rejectExternal(token as string);
      setData((prev) => (prev ? { ...prev, status: "rejected" } : null));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reject failed");
    } finally {
      setActionLoading(false);
    }
  };

  const handleSign = async () => {
    if (!token || !consentText) return;
    setActionLoading(true);
    try {
      await signExternal(token as string, consentText);
      setData((prev) => (prev ? { ...prev, status: "signed" } : null));
      setShowSignForm(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Signing failed");
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-gray-500">Loading agreement...</div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-gray-900 mb-2">
            Unable to load agreement
          </h1>
          <p className="text-gray-500">{error}</p>
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white shadow">
        <div className="max-w-4xl mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-xl font-bold text-gray-900">{data.title}</h1>
              <p className="text-sm text-gray-500">
                From: {data.company_name} • Version {data.version_number}
              </p>
            </div>
            <div className="flex items-center space-x-2">
              <span
                className={`px-3 py-1 text-sm font-medium rounded-full ${
                  data.status === "accepted" || data.status === "signed"
                    ? "bg-green-100 text-green-800"
                    : data.status === "rejected"
                    ? "bg-red-100 text-red-800"
                    : "bg-yellow-100 text-yellow-800"
                }`}
              >
                {data.status.charAt(0).toUpperCase() + data.status.slice(1)}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-4 py-8">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Agreement Content */}
          <div className="lg:col-span-2">
            <div className="bg-white shadow rounded-lg p-6">
              <h2 className="text-lg font-medium text-gray-900 mb-4">
                Agreement Content
              </h2>
              <div className="prose prose-sm max-w-none">
                <pre className="whitespace-pre-wrap text-sm text-gray-700 font-sans">
                  {data.content}
                </pre>
              </div>
            </div>
          </div>

          {/* Actions Sidebar */}
          <div className="space-y-6">
            {/* Status Card */}
            <div className="bg-white shadow rounded-lg p-6">
              <h2 className="text-lg font-medium text-gray-900 mb-4">
                Your Response
              </h2>
              <p className="text-sm text-gray-500 mb-4">
                Review the agreement and respond below.
              </p>

              {data.status === "pending" || data.status === "viewed" ? (
                <div className="space-y-3">
                  <button
                    onClick={handleAccept}
                    disabled={actionLoading}
                    className="w-full px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50"
                  >
                    {actionLoading ? "Processing..." : "Accept Agreement"}
                  </button>
                  <button
                    onClick={handleReject}
                    disabled={actionLoading}
                    className="w-full px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50"
                  >
                    Reject Agreement
                  </button>
                  <button
                    onClick={() => setShowSignForm(true)}
                    disabled={actionLoading}
                    className="w-full px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
                  >
                    Sign Agreement
                  </button>
                </div>
              ) : data.status === "accepted" ? (
                <div className="text-center py-4">
                  <div className="text-green-600 font-medium">
                    ✓ Agreement Accepted
                  </div>
                  <p className="text-sm text-gray-500 mt-2">
                    You have accepted this agreement.
                  </p>
                </div>
              ) : data.status === "signed" ? (
                <div className="text-center py-4">
                  <div className="text-green-600 font-medium">
                    ✓ Agreement Signed
                  </div>
                  <p className="text-sm text-gray-500 mt-2">
                    You have signed this agreement.
                  </p>
                </div>
              ) : data.status === "rejected" ? (
                <div className="text-center py-4">
                  <div className="text-red-600 font-medium">
                    ✗ Agreement Rejected
                  </div>
                  <p className="text-sm text-gray-500 mt-2">
                    You have rejected this agreement.
                  </p>
                </div>
              ) : null}
            </div>

            {/* Sign Form */}
            {showSignForm && (
              <div className="bg-white shadow rounded-lg p-6">
                <h2 className="text-lg font-medium text-gray-900 mb-4">
                  Electronic Signature
                </h2>
                <div className="mb-4">
                  <label className="block text-sm text-gray-700 mb-2">
                    Consent Disclosure
                  </label>
                  <textarea
                    value={consentText}
                    onChange={(e) => setConsentText(e.target.value)}
                    rows={3}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                </div>
                <p className="text-xs text-gray-500 mb-4">
                  By clicking &quot;Sign&quot;, you confirm that you have
                  reviewed the agreement and agree to be bound by its terms.
                  Your signature will be recorded with a timestamp and IP
                  address for legal purposes.
                </p>
                <div className="flex space-x-3">
                  <button
                    onClick={handleSign}
                    disabled={actionLoading || !consentText}
                    className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
                  >
                    {actionLoading ? "Signing..." : "Sign Now"}
                  </button>
                  <button
                    onClick={() => setShowSignForm(false)}
                    className="px-4 py-2 border border-gray-300 rounded-md hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Comment Form */}
            <div className="bg-white shadow rounded-lg p-6">
              <h2 className="text-lg font-medium text-gray-900 mb-4">
                Add Comment
              </h2>
              {commentSuccess && (
                <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded mb-4">
                  Comment submitted successfully
                </div>
              )}
              <div className="space-y-3">
                <div>
                  <label className="block text-sm text-gray-700 mb-1">
                    Clause (optional)
                  </label>
                  <input
                    type="text"
                    value={clauseId}
                    onChange={(e) => setClauseId(e.target.value)}
                    placeholder="e.g., section.12"
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-700 mb-1">
                    Comment
                  </label>
                  <textarea
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    rows={3}
                    placeholder="Your comment or feedback..."
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                </div>
                <button
                  onClick={handleComment}
                  disabled={actionLoading || !comment.trim()}
                  className="w-full px-4 py-2 bg-gray-600 text-white rounded-md hover:bg-gray-700 disabled:opacity-50"
                >
                  {actionLoading ? "Submitting..." : "Submit Comment"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
