"use client";

/**
 * Public external-party signing page — no login required.
 *
 * Flow:
 *  1. Load session from /api/v1/signing/external/{token}
 *  2. Render document preview + signatory details
 *  3. Request OTP via /api/v1/signing/external/{token}/otp
 *  4. On OTP entry, submit signature via /api/v1/signing/external/{token}/sign
 */

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";

interface SigningSession {
  token: string;
  agreement_id: string;
  agreement_title: string;
  organization_name: string;
  signatory_name: string;
  signatory_email: string;
  content_html: string | null;
  content_text: string | null;
  status: string;               // pending | otp_sent | signed | expired | cancelled
  expires_at: string;
}

type Step = "loading" | "preview" | "otp_request" | "otp_verify" | "signed" | "error";

export default function ExternalSignPage() {
  const { token } = useParams<{ token: string }>();
  const [session, setSession] = useState<SigningSession | null>(null);
  const [step, setStep] = useState<Step>("loading");
  const [error, setError] = useState<string | null>(null);
  const [otpCode, setOtpCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const otpRef = useRef<HTMLInputElement>(null);

  // ── Load session ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!token) return;
    fetch(`/api/v1/signing/external/${token}`)
      .then((r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "Signing link not found or expired." : `Error ${r.status}`);
        return r.json() as Promise<SigningSession>;
      })
      .then((s) => {
        setSession(s);
        if (s.status === "signed") { setStep("signed"); return; }
        if (s.status === "expired" || s.status === "cancelled") {
          setError(`This signing link has ${s.status}.`);
          setStep("error");
          return;
        }
        setStep("preview");
      })
      .catch((e) => { setError(e.message); setStep("error"); });
  }, [token]);

  // ── Request OTP ───────────────────────────────────────────────────────────
  const requestOtp = async () => {
    if (!agreed) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`/api/v1/signing/external/${token}/otp`, { method: "POST" });
      if (!r.ok) throw new Error(`OTP request failed (${r.status})`);
      setStep("otp_verify");
      setTimeout(() => otpRef.current?.focus(), 100);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  // ── Submit signature ──────────────────────────────────────────────────────
  const submitSign = async () => {
    if (!otpCode.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`/api/v1/signing/external/${token}/sign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ otp_code: otpCode.trim() }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail ?? `Sign failed (${r.status})`);
      }
      setStep("signed");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-3 shadow-sm">
        <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
          <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        </div>
        <span className="text-sm font-semibold text-gray-800">ContractOS — Secure Signing</span>
        {session && (
          <span className="ml-auto text-xs text-gray-400">
            Sent by {session.organization_name}
          </span>
        )}
      </header>

      <main className="flex-1 flex items-start justify-center px-4 py-10">
        <div className="w-full max-w-3xl">

          {/* ── Loading ── */}
          {step === "loading" && (
            <div className="flex items-center justify-center py-32">
              <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
            </div>
          )}

          {/* ── Error ── */}
          {step === "error" && (
            <div className="bg-white rounded-2xl border border-red-200 shadow-sm p-10 text-center">
              <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-red-100 flex items-center justify-center">
                <svg className="w-7 h-7 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </div>
              <h2 className="text-lg font-semibold text-gray-900 mb-2">Unable to load signing session</h2>
              <p className="text-sm text-gray-500">{error}</p>
            </div>
          )}

          {/* ── Signed (success) ── */}
          {step === "signed" && (
            <div className="bg-white rounded-2xl border border-emerald-200 shadow-sm p-10 text-center">
              <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-emerald-100 flex items-center justify-center">
                <svg className="w-8 h-8 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h2 className="text-xl font-bold text-gray-900 mb-2">Document Signed</h2>
              <p className="text-sm text-gray-500 mb-1">
                Your signature has been recorded for{" "}
                <strong>{session?.agreement_title ?? "this agreement"}</strong>.
              </p>
              <p className="text-xs text-gray-400 mt-4">
                A confirmation will be sent to {session?.signatory_email}.
              </p>
            </div>
          )}

          {/* ── Preview + consent ── */}
          {(step === "preview" || step === "otp_request") && session && (
            <div className="space-y-6">
              {/* Session info card */}
              <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
                <h1 className="text-lg font-bold text-gray-900 mb-1">{session.agreement_title}</h1>
                <p className="text-sm text-gray-500">
                  Signing as <span className="font-medium text-gray-700">{session.signatory_name}</span>
                  {" · "}{session.signatory_email}
                </p>
                <div className="mt-3 text-xs text-gray-400">
                  Link expires: {new Date(session.expires_at).toLocaleString()}
                </div>
              </div>

              {/* Document preview */}
              <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
                  <span className="text-sm font-semibold text-gray-700">Agreement Content</span>
                  <span className="text-xs text-gray-400">Read carefully before signing</span>
                </div>
                <div
                  id="doc-preview"
                  className="px-6 py-5 max-h-[500px] overflow-y-auto text-sm text-gray-700 leading-relaxed prose prose-sm max-w-none"
                  dangerouslySetInnerHTML={
                    session.content_html
                      ? { __html: session.content_html }
                      : undefined
                  }
                >
                  {!session.content_html && (
                    <pre className="whitespace-pre-wrap font-sans">
                      {session.content_text ?? "Document preview unavailable."}
                    </pre>
                  )}
                </div>
              </div>

              {/* Consent checkbox */}
              <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
                <label className="flex items-start gap-3 cursor-pointer" htmlFor="consent-check">
                  <input
                    id="consent-check"
                    type="checkbox"
                    checked={agreed}
                    onChange={(e) => setAgreed(e.target.checked)}
                    className="mt-0.5 w-4 h-4 text-blue-600 rounded border-gray-300 focus:ring-blue-500"
                  />
                  <span className="text-sm text-gray-700">
                    I have read and understood the agreement and consent to sign it electronically.
                    I confirm that I am authorised to sign on behalf of the named party.
                  </span>
                </label>

                {error && (
                  <div className="mt-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
                    {error}
                  </div>
                )}

                <button
                  id="request-otp-btn"
                  onClick={requestOtp}
                  disabled={!agreed || busy}
                  className="mt-5 w-full py-3 rounded-xl text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 transition-colors disabled:opacity-40"
                >
                  {busy ? "Sending code…" : "Send verification code & sign"}
                </button>
                <p className="mt-2 text-xs text-center text-gray-400">
                  A one-time code will be sent to {session.signatory_email}
                </p>
              </div>
            </div>
          )}

          {/* ── OTP verification ── */}
          {step === "otp_verify" && session && (
            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-8 text-center">
              <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-blue-100 flex items-center justify-center">
                <svg className="w-7 h-7 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
              </div>
              <h2 className="text-lg font-bold text-gray-900 mb-1">Check your email</h2>
              <p className="text-sm text-gray-500 mb-6">
                Enter the 6-digit code sent to <strong>{session.signatory_email}</strong>
              </p>

              <input
                id="otp-input"
                ref={otpRef}
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={otpCode}
                onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ""))}
                placeholder="000000"
                className="w-40 text-center text-2xl font-mono tracking-widest rounded-xl border border-gray-200 px-4 py-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />

              {error && (
                <div className="mt-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
                  {error}
                </div>
              )}

              <div className="mt-6 flex flex-col gap-3">
                <button
                  id="submit-sign-btn"
                  onClick={submitSign}
                  disabled={otpCode.length < 4 || busy}
                  className="w-full py-3 rounded-xl text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 transition-colors disabled:opacity-40"
                >
                  {busy ? "Signing…" : "Confirm & Sign"}
                </button>
                <button
                  onClick={() => { setStep("preview"); setOtpCode(""); setError(null); }}
                  className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
                >
                  ← Back
                </button>
              </div>
            </div>
          )}

        </div>
      </main>

      <footer className="py-4 text-center text-xs text-gray-400">
        Powered by ContractOS · Electronic signature compliant with applicable e-signature laws
      </footer>
    </div>
  );
}
