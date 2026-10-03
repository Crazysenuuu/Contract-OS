import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY, LEGAL_CONTACTS, MINIMUM_AGE } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Privacy Policy — ContractOS",
  description:
    "How ContractOS collects, uses, shares, and protects personal data, and the rights available to data subjects.",
};

export default function PrivacyPolicyPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto py-12 px-4 sm:px-6">
        <Link href="/legal" className="text-sm text-gray-500 hover:text-gray-700">
          ← Legal
        </Link>
        <h1 className="text-3xl font-bold text-gray-900 mt-3">
          Privacy Policy
        </h1>
        <p className="text-sm text-gray-500 mt-2">
          Effective {COMPANY.effectiveDate}
        </p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed text-gray-700">
          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              1. Who we are
            </h2>
            <p>
              {COMPANY.legalName} operates the {COMPANY.productName} contract
              lifecycle management platform (the &ldquo;Service&rdquo;). This
              policy explains how we process personal data.
            </p>
            <p className="mt-3">
              <strong>Two roles.</strong> When your organisation uploads
              contracts and documents to the Service, your organisation is the
              data controller and we act as its <em>processor</em>, handling
              that content only on its instructions. For the account,
              security, and website data we collect directly from users and
              visitors, we act as the <em>controller</em>. If you have a
              separate data processing agreement (DPA) with us, that DPA
              governs our processing of Customer Content.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              2. Personal data we collect
            </h2>
            <ul className="list-disc pl-5 space-y-1.5">
              <li>
                <strong>Account data:</strong> name, work email address, hashed
                password, organisation, role, and authentication tokens held in
                your browser.
              </li>
              <li>
                <strong>Customer Content:</strong> contracts, documents, and
                metadata your organisation submits, including text extracted
                by OCR and, for signing workflows, signature and audit-trail
                records.
              </li>
              <li>
                <strong>Security and usage data:</strong> IP address, browser
                and device information, timestamps, and audit logs of actions
                taken in the Service, used to operate, secure, and support the
                Service.
              </li>
              <li>
                <strong>Verification data:</strong> where identity verification
                or age checks are enabled, the minimum information needed to
                verify identity. Date of birth, if provided for an age check,
                is used only to confirm eligibility and is not retained.
              </li>
              <li>
                <strong>Support data:</strong> correspondence you send us and
                contact details you provide.
              </li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              3. How we use personal data
            </h2>
            <ul className="list-disc pl-5 space-y-1.5">
              <li>provide, operate, and improve the Service;</li>
              <li>
                process agreements, extract terms, and generate summaries,
                risk flags, and clause suggestions;
              </li>
              <li>authenticate users and maintain multi-tenant isolation;</li>
              <li>detect, investigate, and prevent security incidents and abuse;</li>
              <li>send transactional and service notifications and reminders;</li>
              <li>comply with legal obligations and enforce our agreements.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              4. Legal bases
            </h2>
            <p>
              Where the EU/UK GDPR or similar law applies, we rely on:{" "}
              <strong>performance of a contract</strong> (providing the
              Service), <strong>legal obligation</strong> (security and
              compliance records), <strong>legitimate interests</strong>{" "}
              (securing and improving the Service, provided these are not
              overridden by your rights), and <strong>consent</strong> where
              required (for example, optional communications).
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              5. Sharing and sub-processors
            </h2>
            <p>
              We do not sell personal data. We share it with service providers
              that help us run the Service, under contracts that restrict them
              to processing on our instructions. Depending on how the Service
              is configured, these may include providers for:
            </p>
            <ul className="list-disc pl-5 space-y-1.5 mt-2">
              <li>cloud storage and hosting (for example, Amazon S3);</li>
              <li>transactional email delivery;</li>
              <li>electronic signature (for example, DocuSign and Adobe Sign);</li>
              <li>identity verification;</li>
              <li>
                AI/LLM processing used for extraction, summarisation, and
                analysis;
              </li>
              <li>push, SMS, and chat notifications; and</li>
              <li>infrastructure caching and background job processing.</li>
            </ul>
            <p className="mt-3">
              We may also disclose personal data where required by law, to
              protect rights and safety, or as part of a merger or acquisition.
            </p>
            <p className="mt-3 text-xs text-gray-500">
              Operator note: publish and maintain a definitive sub-processor
              list (with locations) and keep it in sync with the integrations
              actually enabled for each tenant. Enterprise DPAs require prior
              notice of sub-processor changes.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              6. International transfers
            </h2>
            <p>
              Some providers may process data outside your country. Where we
              transfer personal data internationally, we use appropriate
              safeguards such as standard contractual clauses and, where
              relevant, supplementary measures.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              7. Security
            </h2>
            <p>
              We use technical and organisational measures designed to protect
              personal data, including encryption in transit, field-level
              encryption of sensitive values with cryptographic shredding for
              erasure, tenant-isolation controls, access controls, audit
              logging, and masking of sensitive inputs in tooling. No method of
              storage or transmission is completely secure, so we cannot
              guarantee absolute security.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              8. Retention
            </h2>
            <p>
              We retain personal data for as long as needed to provide the
              Service and to meet legal, accounting, and evidentiary
              requirements. When Customer Content is deleted or an erasure
              request is executed, we apply cryptographic shredding — deleting
              the key material that renders the ciphertext readable — while
              preserving the structural and audit integrity required by law.
              Some records may be retained where we have a legal obligation or
              a lawful basis to do so.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              9. Your rights
            </h2>
            <p>
              Subject to applicable law, you may have the right to access,
              correct, delete, restrict, or object to processing of your
              personal data, to data portability, and to withdraw consent. To
              exercise a right, contact us at{" "}
              <a
                href={`mailto:${LEGAL_CONTACTS.privacy}`}
                className="text-blue-600 hover:text-blue-500"
              >
                {LEGAL_CONTACTS.privacy}
              </a>
              . We will verify your identity and respond within the time
              required by law. If your request concerns Customer Content
              controlled by your organisation, we will refer it to that
              organisation or act on its instructions.
            </p>
            <p className="mt-3">
              You also have the right to lodge a complaint with your local data
              protection authority.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              10. Children
            </h2>
            <p>
              The Service is not directed to children under {MINIMUM_AGE}. We
              do not knowingly collect personal data from children under{" "}
              {MINIMUM_AGE}, and we take steps to delete such data if we learn
              we have collected it.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              11. Cookies and local storage
            </h2>
            <p>
              We use strictly-necessary cookies and local storage to keep you
              signed in and to remember your organisation selection. See our{" "}
              <Link
                href="/legal/cookies"
                className="text-blue-600 hover:text-blue-500"
              >
                Cookie Policy
              </Link>{" "}
              for details.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              12. Changes
            </h2>
            <p>
              We may update this policy from time to time. Material changes
              will be notified by email or in-product notice before they take
              effect.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              13. Contact
            </h2>
            <address className="not-italic rounded-lg bg-white border border-gray-200 p-4">
              <div className="font-medium text-gray-900">
                {COMPANY.legalName}
              </div>
              {COMPANY.addressLines.map((line) => (
                <div key={line}>{line}</div>
              ))}
              <div className="mt-2">
                Privacy:{" "}
                <a
                  href={`mailto:${LEGAL_CONTACTS.privacy}`}
                  className="text-blue-600 hover:text-blue-500"
                >
                  {LEGAL_CONTACTS.privacy}
                </a>
              </div>
              <div>
                Data Protection Officer:{" "}
                <a
                  href={`mailto:${LEGAL_CONTACTS.dpo}`}
                  className="text-blue-600 hover:text-blue-500"
                >
                  {LEGAL_CONTACTS.dpo}
                </a>
              </div>
            </address>
          </section>
        </div>
      </div>
    </div>
  );
}
