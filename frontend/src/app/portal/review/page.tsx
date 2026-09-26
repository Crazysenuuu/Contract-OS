"use client";

/**
 * Guest review view (spec §3.20.19).
 *
 * Token-scoped portal dashboard: shows the agreement the guest's party row
 * points at, their capabilities and verification state. Every read is
 * server-side authorization against the ExternalParty grant — no workspace
 * data beyond that grant is ever requested or rendered.
 */

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

export const dynamic = "force-dynamic";

interface PortalDashboard {
  agreement: {
    id: string;
    title: string | null;
    status: string | null;
    governing_law: string | null;
  };
  party_status: string;
  capabilities: {
    can_comment: boolean;
    can_propose_changes: boolean;
    can_accept: boolean;
    can_sign: boolean;
  };
  requires_id_verification: boolean;
  id_verified: boolean;
  expires_at: string | null;
}

function ReviewInner() {
  const params = useSearchParams();
  const token = params.get("token");
  const [data, setData] = useState<PortalDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    fetch(
      `${process.env.NEXT_PUBLIC_API_URL || ""}/api/v1/external-policy/review/${encodeURIComponent(token)}/dashboard`
    )
      .then(async (res) => {
        if (!res.ok) {
          setError("This access link is invalid or has expired.");
          return;
        }
        setData(await res.json());
      })
      .catch(() => setError("Unable to reach the portal. Try again later."));
  }, [token]);

  if (error) {
    return (
      <main className="mx-auto max-w-lg px-4 py-16">
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-800">
          {error}
        </div>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-gray-500">Loading…</p>
      </main>
    );
  }

  const caps = data.capabilities;
  return (
    <main className="mx-auto max-w-2xl px-4 py-12">
      <p className="text-xs uppercase tracking-wide text-gray-500">
        External review portal
      </p>
      <h1 className="text-2xl font-bold mt-1 mb-1">
        {data.agreement.title || "Shared agreement"}
      </h1>
      <p className="text-sm text-gray-600 mb-6">
        Status: <span className="font-medium">{data.agreement.status}</span>
        {data.agreement.governing_law && (
          <> · Governing law: {data.agreement.governing_law}</>
        )}
      </p>

      {data.requires_id_verification && !data.id_verified && (
        <div className="mb-6 rounded-lg bg-amber-50 border border-amber-200 p-4 text-sm text-amber-800">
          Identity verification is required before you can accept or sign.
        </div>
      )}

      <section className="rounded-lg border border-gray-200 bg-white shadow-sm p-5">
        <h2 className="text-sm font-semibold mb-3">Your access</h2>
        <ul className="text-sm text-gray-700 space-y-1.5">
          <li>Comment on the agreement: {caps.can_comment ? "allowed" : "not allowed"}</li>
          <li>Propose changes: {caps.can_propose_changes ? "allowed" : "not allowed"}</li>
          <li>Accept the agreement: {caps.can_accept ? "allowed" : "not allowed"}</li>
          <li>Sign the agreement: {caps.can_sign ? "allowed" : "not allowed"}</li>
          {data.expires_at && (
            <li className="text-gray-500">
              Access expires: {new Date(data.expires_at).toLocaleString()}
            </li>
          )}
        </ul>
      </section>
    </main>
  );
}

export default function PortalReviewPage() {
  return (
    <Suspense fallback={null}>
      <ReviewInner />
    </Suspense>
  );
}
