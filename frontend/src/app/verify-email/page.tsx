"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { verifyEmail } from "@/lib/api";

function VerifyEmailPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token");
  const [status, setStatus] = useState<
    "idle" | "verifying" | "success" | "error"
  >(token ? "verifying" : "idle");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token || status !== "verifying") return;

    verifyEmail(token)
      .then(() => {
        setStatus("success");
        // No session is created at verification time: registration does not
        // persist tokens while the account is unverified, so there is
        // nothing to carry the user into the app. Sign in with the address
        // they just confirmed.
        setTimeout(() => router.push("/login?verified=1"), 2000);
      })
      .catch((err) => {
        setError(
          err instanceof Error ? err.message : "Verification failed"
        );
        setStatus("error");
      });
  }, [token, status, router]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full space-y-6 p-8">
        <h1 className="text-3xl font-bold text-center text-gray-900">
          ContractOS
        </h1>

        {status === "idle" && (
          <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200 text-center space-y-4">
            <p className="text-sm text-gray-600">
              This link is missing a verification token. Please use the link
              from your welcome email.
            </p>
            <Link
              href="/login"
              className="inline-block px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700"
            >
              Go to sign in
            </Link>
          </div>
        )}

        {status === "verifying" && (
          <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200 text-center space-y-4">
            <p className="text-sm text-gray-600">Verifying your email...</p>
          </div>
        )}

        {status === "success" && (
          <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200 text-center space-y-4">
            <div className="mx-auto h-12 w-12 rounded-full bg-green-100 flex items-center justify-center">
              <svg
                className="h-6 w-6 text-green-600"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M5 13l4 4L19 7"
                />
              </svg>
            </div>
            <p className="text-sm text-gray-700">
              Your email has been verified. Your account is now active.
            </p>
            <p className="text-xs text-gray-500">
              Taking you to sign in&hellip;
            </p>
            <Link
              href="/login"
              className="inline-block px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700"
            >
              Sign in now
            </Link>
          </div>
        )}

        {status === "error" && (
          <div className="bg-white p-6 rounded-lg shadow-sm border border-red-200 text-center space-y-4">
            <p className="text-sm text-red-700">{error}</p>
            <Link
              href="/login"
              className="inline-block px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700"
            >
              Go to sign in
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

export default function VerifyEmail() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-gray-50">
          <p className="text-sm text-gray-600">Loading...</p>
        </div>
      }
    >
      <VerifyEmailPage />
    </Suspense>
  );
}