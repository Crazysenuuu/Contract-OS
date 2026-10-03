import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY, LEGAL_CONTACTS, MINIMUM_AGE } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Terms of Service — ContractOS",
  description:
    "The terms that govern your use of the ContractOS contract lifecycle management platform.",
};

export default function TermsOfServicePage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto py-12 px-4 sm:px-6">
        <Link href="/legal" className="text-sm text-gray-500 hover:text-gray-700">
          ← Legal
        </Link>
        <h1 className="text-3xl font-bold text-gray-900 mt-3">
          Terms of Service
        </h1>
        <p className="text-sm text-gray-500 mt-2">
          Effective {COMPANY.effectiveDate}
        </p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed text-gray-700">
          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              1. Agreement to these Terms
            </h2>
            <p>
              These Terms of Service (&ldquo;Terms&rdquo;) are a binding
              agreement between you and {COMPANY.legalName}
              (&ldquo;{COMPANY.productName}&rdquo;, &ldquo;we&rdquo;,
              &ldquo;us&rdquo;) governing your access to and use of the{" "}
              {COMPANY.productName} contract lifecycle management platform and
              related services (the &ldquo;Service&rdquo;). By creating an
              account, accessing, or using the Service, you agree to these
              Terms. If you use the Service on behalf of an organisation, you
              represent that you are authorised to bind that organisation, and
              &ldquo;you&rdquo; includes that organisation.
            </p>
            <p className="mt-3">
              If you do not agree to these Terms, do not use the Service. If
              you have a separate signed agreement with us that conflicts with
              these Terms, the signed agreement controls for that organisation.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              2. Eligibility and accounts
            </h2>
            <p>
              You must be at least {MINIMUM_AGE} years old to use the Service.
              You must provide accurate registration information and keep it
              current. You are responsible for safeguarding your credentials
              and for all activity under your account. Notify us promptly at{" "}
              <a
                href={`mailto:${LEGAL_CONTACTS.security}`}
                className="text-blue-600 hover:text-blue-500"
              >
                {LEGAL_CONTACTS.security}
              </a>{" "}
              of any suspected unauthorised access.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              3. Acceptable use
            </h2>
            <p>You agree not to:</p>
            <ul className="list-disc pl-5 space-y-1.5 mt-2">
              <li>
                use the Service in violation of any applicable law or
                regulation, or to infringe the rights of others;
              </li>
              <li>
                upload malware, attempt to gain unauthorised access to the
                Service or its underlying infrastructure, or probe, scan, or
                test its vulnerability without our written permission;
              </li>
              <li>
                reverse engineer, resell, or provide the Service to third
                parties except as expressly permitted; or
              </li>
              <li>
                use the Service to make automated decisions about individuals
                that are prohibited by law or that require human review which
                you fail to provide.
              </li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              4. Customer Content
            </h2>
            <p>
              You and your organisation retain all rights in the contracts,
              documents, and other content you submit to the Service
              (&ldquo;Customer Content&rdquo;). You grant us a limited licence
              to host, process, transmit, and display Customer Content solely
              to provide and secure the Service and as instructed by your
              organisation. Our handling of personal data within Customer
              Content is described in our{" "}
              <Link
                href="/legal/privacy"
                className="text-blue-600 hover:text-blue-500"
              >
                Privacy Policy
              </Link>
              .
            </p>
            <p className="mt-3">
              You are responsible for having the necessary rights and lawful
              basis to submit Customer Content and for the accuracy of the
              information in it.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              5. AI-assisted features
            </h2>
            <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 not-prose">
              <p className="text-amber-900">
                <strong>Not legal advice.</strong> The Service uses automated
                and AI-assisted tools to extract data, summarise clauses, flag
                risks, and generate suggestions. Output may be incomplete or
                incorrect. It is provided for informational purposes to support
                your review, and does not constitute legal advice. You are
                responsible for reviewing all output and for any decision you
                make based on it.
              </p>
            </div>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              6. Electronic signatures
            </h2>
            <p>
              Where you use e-signature integrations, you are responsible for
              obtaining valid consent and for complying with the electronic
              signature laws applicable to your transaction (for example the
              U.S. ESIGN Act, UETA, or the EU eIDAS Regulation). Signature
              records and audit trails are maintained as described in the
              Privacy Policy.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              7. Fees
            </h2>
            <p>
              Paid plans, billing cycles, and taxes are set out in the order or
              plan you agree to with us. Fees are non-refundable except where
              required by law or expressly stated. We may suspend the Service
              for overdue undisputed invoices after notice.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              8. Intellectual property
            </h2>
            <p>
              The Service, including its software, design, and documentation,
              is owned by us and our licensors and is protected by intellectual
              property laws. These Terms do not transfer any ownership of the
              Service to you; we grant you a limited, non-exclusive,
              non-transferable right to use the Service during your
              subscription.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              9. Disclaimers
            </h2>
            <p>
              Except as expressly stated, the Service is provided
              &ldquo;as is&rdquo; and &ldquo;as available&rdquo;, without
              warranties of any kind, whether express, implied, or statutory,
              including fitness for a particular purpose, merchantability, and
              non-infringement. We do not warrant that the Service will be
              uninterrupted, error-free, or that it will meet your
              requirements.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              10. Limitation of liability
            </h2>
            <p>
              To the maximum extent permitted by law, neither party is liable
              for indirect, incidental, special, consequential, or punitive
              damages, or for lost profits or lost data. Our aggregate
              liability arising out of or relating to these Terms is limited to
              the amounts you paid for the Service in the twelve months before
              the event giving rise to the claim. Nothing in these Terms
              excludes liability that cannot be excluded by law.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              11. Term and termination
            </h2>
            <p>
              These Terms apply while you use the Service. You may stop using
              the Service at any time. We may suspend or terminate access for
              material breach, for conduct that endangers the Service or other
              users, or as required by law. On termination, your right to use
              the Service ends; the sections that by their nature should
              survive (including intellectual property, disclaimers, liability,
              and governing law) will survive.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              12. Changes to these Terms
            </h2>
            <p>
              We may update these Terms from time to time. For material changes
              we will provide reasonable notice (for example by email or an
              in-product notice). Continuing to use the Service after the
              effective date of the updated Terms constitutes acceptance.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              13. Governing law and disputes
            </h2>
            <p>
              These Terms are governed by {COMPANY.governingLaw}, without
              regard to its conflict-of-law rules. The courts located in
              Colombo, Sri Lanka have exclusive jurisdiction, unless mandatory
              law in your jurisdiction provides otherwise.
            </p>
            <p className="mt-3 text-xs text-gray-500">
              Operator note: confirm the governing-law and venue clauses with
              counsel for your target markets before relying on them. Many
              enterprise customers expect their own governing law and venue.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              14. Contact
            </h2>
            <p>
              Questions about these Terms:{" "}
              <a
                href={`mailto:${LEGAL_CONTACTS.general}`}
                className="text-blue-600 hover:text-blue-500"
              >
                {LEGAL_CONTACTS.general}
              </a>
              .
            </p>
            <address className="not-italic mt-3 rounded-lg bg-white border border-gray-200 p-4">
              <div className="font-medium text-gray-900">
                {COMPANY.legalName}
              </div>
              {COMPANY.addressLines.map((line) => (
                <div key={line}>{line}</div>
              ))}
            </address>
          </section>
        </div>
      </div>
    </div>
  );
}
