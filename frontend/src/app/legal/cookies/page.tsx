import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY, LEGAL_CONTACTS } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Cookie Policy — ContractOS",
  description:
    "The cookies and browser storage ContractOS uses, and the tracking technologies it does not.",
};

const STORAGE_ITEMS = [
  {
    name: "token",
    type: "localStorage",
    purpose: "Short-lived access credential that keeps you signed in.",
    duration: "Cleared on logout or session expiry.",
  },
  {
    name: "refresh_token",
    type: "localStorage",
    purpose: "Used to obtain a new access credential without re-entering your password.",
    duration: "Cleared on logout or when refresh is rejected.",
  },
  {
    name: "contractos.currentOrganizationId",
    type: "localStorage",
    purpose: "Remembers which organisation you selected so requests are scoped correctly.",
    duration: "Cleared on logout.",
  },
] as const;

export default function CookiePolicyPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto py-12 px-4 sm:px-6">
        <Link href="/legal" className="text-sm text-gray-500 hover:text-gray-700">
          ← Legal
        </Link>
        <h1 className="text-3xl font-bold text-gray-900 mt-3">Cookie Policy</h1>
        <p className="text-sm text-gray-500 mt-2">
          Effective {COMPANY.effectiveDate}
        </p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed text-gray-700">
          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              1. Our approach
            </h2>
            <p>
              {COMPANY.productName} is a business application, not an
              advertising or content site. We keep browser-side storage to the
              minimum needed to operate securely. At this time, the Service
              does not set advertising, analytics, or cross-site tracking
              cookies.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              2. Strictly-necessary browser storage
            </h2>
            <p>
              Rather than cookies, the Service stores a small set of
              strictly-necessary values in your browser&rsquo;s local storage.
              These are required for authentication, security, and correct
              multi-tenant behaviour, so they cannot be switched off while you
              use the Service.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left border border-gray-200 rounded-lg bg-white">
                <thead className="bg-gray-50 text-gray-900">
                  <tr>
                    <th className="px-4 py-2 font-semibold">Name</th>
                    <th className="px-4 py-2 font-semibold">Type</th>
                    <th className="px-4 py-2 font-semibold">Purpose</th>
                    <th className="px-4 py-2 font-semibold">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {STORAGE_ITEMS.map((item) => (
                    <tr key={item.name} className="border-t border-gray-200">
                      <td className="px-4 py-2 font-mono text-xs break-all">
                        {item.name}
                      </td>
                      <td className="px-4 py-2">{item.type}</td>
                      <td className="px-4 py-2">{item.purpose}</td>
                      <td className="px-4 py-2">{item.duration}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              3. Session replay and error tooling
            </h2>
            <p>
              Session replay and diagnostic recording are disabled by default.
              When enabled by an operator, sensitive inputs (passwords, one-time
              codes, card numbers, and dates of birth) are masked so they are
              never captured.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              4. Managing browser storage
            </h2>
            <p>
              You can clear local storage through your browser settings, which
              will sign you out. Because these values are strictly necessary,
              blocking them prevents the Service from functioning.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              5. Contact
            </h2>
            <p>
              Questions about this policy:{" "}
              <a
                href={`mailto:${LEGAL_CONTACTS.privacy}`}
                className="text-blue-600 hover:text-blue-500"
              >
                {LEGAL_CONTACTS.privacy}
              </a>
              . See also our{" "}
              <Link
                href="/legal/privacy"
                className="text-blue-600 hover:text-blue-500"
              >
                Privacy Policy
              </Link>
              .
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
