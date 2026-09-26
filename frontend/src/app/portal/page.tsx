"use client";

/**
 * External portal landing (spec §3.20.18).
 *
 * Guests reach their agreement view through tokenized links. This page is
 * the fallback entry: token links redirect to the review experience; guests
 * without a link can request re-delivery via the anti-enumeration lookup
 * (§3.20.57) — the response never reveals whether an access exists.
 */

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

export const dynamic = "force-dynamic";

async function portalLookup(email: string) {
  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL || ""}/api/v1/external-policy/portal/lookup`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    }
  );
  // Uniform outcome regardless of account existence (anti-enumeration).
  return res.ok;
}

function PortalInner() {
  const params = useSearchParams();
  const token = params.get("token");
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);

  useEffect(() => {
    if (token) {
      window.location.href = `/portal/review?token=${encodeURIComponent(token)}`;
    }
  }, [token]);

  if (token) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-gray-500">Opening your agreement…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-bold mb-2">Contract Portal</h1>
      <p className="text-sm text-gray-600 mb-8">
        Access to shared agreements is by invitation link. If you lost yours,
        enter the email the invitation was sent to and we will re-send the
        link.
      </p>

      {sent ? (
        <div className="rounded-lg bg-green-50 border border-green-200 p-4 text-sm text-green-800">
          If a portal access exists for this address, the link has been
          re-sent.
        </div>
      ) : (
        <div className="space-y-3">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
          <button
            onClick={requestLink}
            disabled={!email.includes("@")}
            className="w-full rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Re-send my access link
          </button>
        </div>
      )}
    </main>
  );

  async function requestLink() {
    try {
      await portalLookup(email);
    } catch {
      // transport errors surface the same uniform message
    }
    setSent(true);
  }
}

export default function PortalPage() {
  return (
    <Suspense fallback={null}>
      <PortalInner />
    </Suspense>
  );
}
