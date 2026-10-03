"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { register } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import LegalFooter from "@/components/LegalFooter";

export default function RegisterPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // COPPA age gate: collected for the 13+ check only. The backend stores a
  // derived boolean, never the date itself.
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  // Set once the backend confirms the account exists and the verification
  // email is on its way. Registration no longer lands the user in the app:
  // the backend rejects unverified accounts, so redirecting to /dashboard
  // would only produce a 401 loop.
  const [pendingEmail, setPendingEmail] = useState<string | null>(null);
  const router = useRouter();
  const { login: authLogin } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    // Client-side mirror of the server-side 13+ check.
    if (dateOfBirth) {
      const dob = new Date(dateOfBirth + "T00:00:00");
      const today = new Date();
      let age = today.getFullYear() - dob.getFullYear();
      const beforeBirthday =
        today.getMonth() < dob.getMonth() ||
        (today.getMonth() === dob.getMonth() && today.getDate() < dob.getDate());
      if (beforeBirthday) age -= 1;
      if (age < 13) {
        setError("You must be at least 13 years old to create an account.");
        return;
      }
    }

    setLoading(true);
    try {
      const result = await register({ name, email, password, date_of_birth: dateOfBirth });

      if (result.verification_required) {
        // No session is stored until the address is confirmed.
        setPendingEmail(email);
        return;
      }

      await authLogin(result.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  if (pendingEmail) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="max-w-md w-full space-y-6 p-8">
          <div>
            <h1 className="text-3xl font-bold text-center text-gray-900">
              ContractOS
            </h1>
            <h2 className="mt-2 text-center text-sm text-gray-600">
              Check your inbox
            </h2>
          </div>

          <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200 text-center space-y-4">
            <div className="mx-auto h-12 w-12 rounded-full bg-blue-100 flex items-center justify-center">
              <svg
                className="h-6 w-6 text-blue-600"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M3 8l18 8-18 8V8z"
                />
              </svg>
            </div>
            <p className="text-sm text-gray-700">
              We sent a confirmation link to{" "}
              <span className="font-medium">{pendingEmail}</span>. Confirm your
              address to activate your account.
            </p>
            <p className="text-xs text-gray-500">
              The link expires, so verify soon. Nothing works until you do.
            </p>
            <Link
              href="/login"
              className="inline-block px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700"
            >
              Go to sign in
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full space-y-8 p-8">
        <div>
          <h1 className="text-3xl font-bold text-center text-gray-900">
            ContractOS
          </h1>
          <h2 className="mt-2 text-center text-sm text-gray-600">
            Create your account
          </h2>
        </div>

        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
              {error}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label
                htmlFor="name"
                className="block text-sm font-medium text-gray-700"
              >
                Full name
              </label>
              <input
                id="name"
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-gray-700"
              >
                Email address
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-gray-700"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                // Mirrors MIN_LENGTH/MAX_LENGTH in
                // backend/app/core/password_policy.py. The hint below is a
                // UX courtesy, not the enforcement point; the server
                // re-checks everything.
                minLength={12}
                maxLength={128}
                aria-describedby="password-requirements"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
              />
              <p id="password-requirements" className="mt-1 text-xs text-gray-500">
                At least 12 characters. A long passphrase works well and needs
                no symbols or numbers. Avoid common passwords and anything
                containing your name or email.
              </p>
            </div>

            <div>
              <label
                htmlFor="date_of_birth"
                className="block text-sm font-medium text-gray-700"
              >
                Date of birth
              </label>
              <input
                id="date_of_birth"
                name="date_of_birth"
                type="date"
                required
                max={new Date().toISOString().slice(0, 10)}
                aria-describedby="dob-privacy-note"
                value={dateOfBirth}
                onChange={(e) => setDateOfBirth(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
              />
              <p id="dob-privacy-note" className="mt-1 text-xs text-gray-500">
                Used only to confirm you are 13 or older. We never store your
                date of birth.
              </p>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50"
          >
            {loading ? "Creating account..." : "Create account"}
          </button>

          <p className="text-center text-xs text-gray-400">
            By creating an account you agree to our{" "}
            <Link href="/legal/terms" className="hover:text-gray-500">
              Terms of Service
            </Link>{" "}
            and{" "}
            <Link href="/legal/privacy" className="hover:text-gray-500">
              Privacy Policy
            </Link>
            .
          </p>

          <p className="text-center text-sm text-gray-600">
            Already have an account?{" "}
            <Link
              href="/login"
              className="font-medium text-blue-600 hover:text-blue-500"
            >
              Sign in
            </Link>
          </p>

          <LegalFooter />
        </form>
      </div>
    </div>
  );
}
