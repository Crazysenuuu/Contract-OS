import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Legal — ContractOS",
  description:
    "Terms of Service, Privacy Policy, Cookie Policy, and copyright policy for ContractOS.",
};

const POLICIES = [
  {
    href: "/legal/terms",
    title: "Terms of Service",
    description:
      "The agreement between you and ContractOS governing use of the platform.",
  },
  {
    href: "/legal/privacy",
    title: "Privacy Policy",
    description:
      "What personal data ContractOS processes, why, and the rights you have over it.",
  },
  {
    href: "/legal/cookies",
    title: "Cookie Policy",
    description:
      "The strictly-necessary cookies and local storage ContractOS uses, and what it does not.",
  },
  {
    href: "/legal/dmca",
    title: "Copyright / DMCA Policy",
    description:
      "How to submit a copyright takedown notice to our designated agent.",
  },
] as const;

export default function LegalIndexPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto py-12 px-4 sm:px-6">
        <Link href="/" className="text-sm text-gray-500 hover:text-gray-700">
          ← {COMPANY.productName}
        </Link>
        <h1 className="text-3xl font-bold text-gray-900 mt-3">Legal</h1>
        <p className="text-sm text-gray-500 mt-2">
          Policies for the {COMPANY.productName} platform.
        </p>

        <ul className="mt-8 space-y-4">
          {POLICIES.map((policy) => (
            <li key={policy.href}>
              <Link
                href={policy.href}
                className="block rounded-lg bg-white border border-gray-200 p-5 hover:border-blue-400 transition-colors"
              >
                <span className="font-semibold text-blue-700">
                  {policy.title}
                </span>
                <span className="block text-sm text-gray-600 mt-1">
                  {policy.description}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
